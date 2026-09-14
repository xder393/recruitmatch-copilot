"""Actual task entrypoints and effective timing contracts, with external work faked."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.config import ConfigError, Settings
from app.processing.outcomes import ProcessDisposition
from app.tasks import celery_app as module


@pytest.mark.parametrize("outcome", [item for item in ProcessDisposition if item != ProcessDisposition.RETRY_SHORT])
@pytest.mark.parametrize("kind", ["resume", "knowledge"])
def test_normal_outcomes_ack_without_retry(monkeypatch, outcome, kind):
    def process(tenant, source):
        assert (tenant, source) == ("synthetic-tenant", "synthetic-source")
        return outcome

    processor = SimpleNamespace(process=process)
    monkeypatch.setattr(
        module,
        "build_worker_dependencies",
        lambda settings: SimpleNamespace(resume_processor=processor, knowledge_processor=processor),
    )
    task = getattr(module, f"process_{kind}_task")
    monkeypatch.setattr(task, "retry", lambda **kwargs: pytest.fail("normal outcome created retry"))
    assert task.run("synthetic-tenant", "synthetic-source") is None


def test_actual_celery_has_no_result_persistence_and_bounded_delivery():
    conf = module.celery_app.conf
    assert conf.task_ignore_result is True
    assert conf.task_store_errors_even_if_ignored is False
    assert conf.result_backend is None
    assert conf.task_acks_late and conf.task_reject_on_worker_lost
    assert conf.worker_prefetch_multiplier == 1
    assert 0 < conf.task_soft_time_limit < conf.task_time_limit
    assert conf.broker_transport_options["visibility_timeout"] > conf.task_time_limit


def test_result_backend_environment_cannot_override_broker_only_policy(monkeypatch):
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://synthetic.invalid/1")
    with pytest.raises(ConfigError, match="result backend"):
        Settings.load()


@pytest.mark.parametrize("kind", ["resume", "knowledge"])
def test_actual_retry_publish_failure_acks_without_losing_durable_work(monkeypatch, kind):
    task = getattr(module, f"process_{kind}_task")
    processor = SimpleNamespace(process=lambda *_: ProcessDisposition.RETRY_SHORT)
    monkeypatch.setattr(
        module,
        "build_worker_dependencies",
        lambda _: SimpleNamespace(resume_processor=processor, knowledge_processor=processor),
    )

    def broker_down(*args, **kwargs):
        raise OSError("private broker detail")

    monkeypatch.setattr(task, "apply_async", broker_down)
    task.push_request(
        called_directly=False,
        is_eager=False,
        retries=999,
        args=("private-tenant", "private-source"),
        kwargs={},
        id="synthetic-task",
    )
    try:
        assert task.run("private-tenant", "private-source") is None
    finally:
        task.pop_request()


def test_unknown_task_fault_is_a_real_sanitized_failure(monkeypatch, caplog):
    def broken(*args):
        raise RuntimeError("secret-exception-detail")

    monkeypatch.setattr(module, "build_worker_dependencies", broken)
    with caplog.at_level("INFO", logger="celery.app.trace"):
        result = module.process_resume_task.apply(args=("private-tenant", "private-source"), throw=False)
    assert result.state == "FAILURE"
    assert str(result.result) == "processing_task_failed"
    for record in caplog.records:
        rendered = record.getMessage() + str(getattr(record, "data", {}))
        assert "secret-exception-detail" not in rendered
        assert "private-tenant" not in rendered and "private-source" not in rendered


def test_database_unavailable_ack_leaves_recovery_to_postgresql(monkeypatch, caplog):
    from app.repositories.ports import PersistenceUnavailable

    def unavailable(*args):
        raise PersistenceUnavailable("private-db-detail")

    monkeypatch.setattr(module, "build_worker_dependencies", unavailable)
    monkeypatch.setattr(module.process_resume_task, "retry", lambda **_: pytest.fail("DB outage broker retry"))
    assert module.process_resume_task.run("private-tenant", "private-source") is None
    assert "private-db-detail" not in caplog.text
    assert "processing_database_unavailable" in caplog.text


@pytest.mark.parametrize(
    "short,hard,margin,visibility,valid",
    [
        (30, 600, 30, 630, False),
        (30, 600, 30, 631, True),
        (700, 600, 30, 730, False),
        (700, 600, 30, 731, True),
    ],
)
def test_visibility_strictly_covers_largest_countdown_or_hard_limit(short, hard, margin, visibility, valid):
    settings = replace(
        Settings(),
        retry_short_seconds=short,
        retry_long_seconds=800,
        task_hard_time_limit=hard,
        retry_safety_margin_seconds=margin,
        redis_visibility_timeout=visibility,
    )
    if valid:
        settings.validate()
    else:
        with pytest.raises(ConfigError):
            settings.validate()


@pytest.mark.parametrize(
    "field,value",
    [
        ("processing_lease_seconds", 86401),
        ("processing_lease_seconds", 0),
        ("task_soft_time_limit", float("nan")),
        ("task_hard_time_limit", float("inf")),
        ("retry_short_seconds", 0),
        ("retry_long_seconds", -1),
        ("processing_max_attempts", 0),
        ("retry_safety_margin_seconds", 0),
        ("redis_visibility_timeout", 630),
        ("retry_short_seconds", 700),
    ],
)
def test_invalid_timing_or_retry_budget_is_rejected(field, value):
    assert hasattr(Settings(), field), f"missing validated {field}"
    with pytest.raises(ConfigError):
        replace(Settings(), **{field: value}).validate()


def test_task_short_retry_uses_configured_countdown_and_not_broker_attempt_budget(monkeypatch):
    settings = Settings()
    assert hasattr(settings, "retry_short_seconds")
    processor = SimpleNamespace(process=lambda *_: ProcessDisposition.RETRY_SHORT)
    monkeypatch.setattr(module.Settings, "load", lambda: replace(settings, retry_short_seconds=17))
    monkeypatch.setattr(module, "build_worker_dependencies", lambda _: SimpleNamespace(resume_processor=processor))
    calls = []

    def retry(**kwargs):
        calls.append(kwargs)
        return RuntimeError("synthetic retry signal")

    monkeypatch.setattr(module.process_resume_task, "retry", retry)
    with pytest.raises(RuntimeError, match="synthetic retry signal"):
        module.process_resume_task.run("synthetic-tenant", "synthetic-source")
    assert calls == [{"countdown": 17}]
    assert module.process_resume_task.max_retries is None
