from __future__ import annotations

import pytest


def _setup(tmp_path):
    from app.database import Base, create_engine_and_session
    from app.domain.enums import JobStatus, ResumeStatus, Role
    from app.models import Job, JobVersion, Resume, ResumeArtifact, Tenant, User
    from app.resumes.parser import HeuristicResumeParser
    from app.security.tokens import Principal

    engine, factory = create_engine_and_session(f"sqlite:///{tmp_path / 'service.db'}")
    Base.metadata.create_all(engine)
    with factory() as session:
        acme, globex = Tenant(name="Acme"), Tenant(name="Globex")
        session.add_all([acme, globex])
        session.flush()
        admin = User(tenant=acme, email="admin@acme.test", password_hash="x", role=Role.ADMIN)
        session.add(admin)
        session.flush()
        text = "5 年 Python FastAPI RAG 项目经验"
        resume = Resume(
            tenant_id=acme.id,
            uploaded_by=admin.id,
            sha256="abc",
            original_filename="a.txt",
            media_type="text/plain",
            size_bytes=len(text.encode()),
            status=ResumeStatus.SUCCEEDED,
            profile=HeuristicResumeParser().parse(text).model_dump(mode="json"),
            artifact=ResumeArtifact(storage_key="acme/a.txt", extracted_text=text),
        )
        definitions = [
            ("AI", JobStatus.ACTIVE, ["Python", "FastAPI"], ["RAG"]),
            ("Backend", JobStatus.ACTIVE, ["Python", "MySQL"], ["Redis"]),
            ("Data", JobStatus.ACTIVE, ["Python", "SQL"], ["Spark"]),
            ("Frontend", JobStatus.ACTIVE, ["JavaScript", "React"], ["TypeScript"]),
            ("Inactive perfect", JobStatus.INACTIVE, ["Python"], ["RAG"]),
        ]
        for title, status, required, preferred in definitions:
            job = Job(tenant=acme, title=title, status=status, current_version=1)
            job.versions.append(
                JobVersion(
                    version=1,
                    jd_text=title,
                    profile={
                        "required_skills": required,
                        "preferred_skills": preferred,
                        "min_experience_years": 3,
                        "weights": {"skills": 0.5, "experience": 0.3, "projects": 0.2},
                    },
                    created_by=admin.id,
                )
            )
            session.add(job)
        foreign = Job(tenant=globex, title="Foreign perfect", status=JobStatus.ACTIVE, current_version=1)
        foreign.versions.append(JobVersion(version=1, jd_text="Python RAG", profile={"required_skills": ["Python"]}))
        session.add_all([resume, foreign])
        session.commit()
        principal = Principal(user_id=admin.id, tenant_id=acme.id, role=Role.ADMIN)
        foreign_principal = Principal(user_id="globex", tenant_id=globex.id, role=Role.ADMIN)
        return factory, principal, foreign_principal, resume.id


def test_run_persists_top_three_from_active_same_tenant_jobs(tmp_path):
    """Catches inactive or foreign jobs entering candidate recommendations."""
    from app.domain.enums import MatchStatus
    from app.services.matching import MatchingService
    from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork

    factory, principal, _, resume_id = _setup(tmp_path)
    with factory() as session:
        uow = SqlAlchemyUnitOfWork(session)
        run = MatchingService(uow.matching, uow=uow).run(principal, resume_id)
        assert run.status is MatchStatus.SUCCEEDED
        assert run.algorithm_version == "rules-v1"
        assert [result.rank for result in run.results] == [1, 2, 3]
        titles = [result.job_version.job.title for result in run.results]
        assert titles == ["AI", "Backend", "Data"]
        assert "Inactive perfect" not in titles
        assert "Foreign perfect" not in titles


def test_run_hides_resume_from_another_tenant(tmp_path):
    """Catches caller-controlled resume IDs bypassing tenant predicates."""
    from app.core.exceptions import ResourceNotFoundError
    from app.services.matching import MatchingService
    from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork

    factory, _, foreign_principal, resume_id = _setup(tmp_path)
    with factory() as session:
        with pytest.raises(ResourceNotFoundError):
            uow = SqlAlchemyUnitOfWork(session)
            MatchingService(uow.matching, uow=uow).run(foreign_principal, resume_id)


def test_scope_keeps_old_knowledge_generation_authorized_during_refresh():
    from types import SimpleNamespace

    from app.services.matching import MatchingService

    documents = [
        SimpleNamespace(id="knowledge-1", checksum="checksum", status="processing", search_index_status="ready"),
        SimpleNamespace(id="knowledge-2", checksum="checksum-2", status="inactive", search_index_status="ready"),
    ]
    uow = SimpleNamespace(knowledge=SimpleNamespace(list_documents=lambda tenant_id: documents))
    service = MatchingService(SimpleNamespace(), uow=uow)
    resume = SimpleNamespace(id="resume-1", sha256="sha")
    candidate = SimpleNamespace(job_version_id="job-version-1")

    scope = service._search_scope("tenant-1", resume, [candidate], {"job-version-1": "1"})

    assert ("knowledge_document", "knowledge-1", "checksum") in scope.authorized_sources
    assert ("knowledge_document", "knowledge-2", "checksum-2") not in scope.authorized_sources
