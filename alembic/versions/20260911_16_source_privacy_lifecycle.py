"""Separate Source privacy from processing and remove legacy tombstone content.

Revision ID: 20260911_16
Revises: 20260911_15
"""

from alembic import op
import sqlalchemy as sa

revision = "20260911_16"
down_revision = "20260911_15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    # An old Knowledge status is not consent to erase unrelated match history.
    # Abort before DDL/data writes unless the history is already empty.
    if connection.scalar(
        sa.text("""
        SELECT EXISTS (
            SELECT 1 FROM knowledge_documents d JOIN match_runs r ON r.tenant_id=d.tenant_id
            JOIN match_results m ON m.run_id=r.id
            WHERE d.status='deleted' AND (
                m.summary IS NOT NULL OR COALESCE(m.dimension_scores::jsonb,'{}') <> '{}'::jsonb
                OR COALESCE(m.grounded_explanation::jsonb,'{}') <> '{}'::jsonb
                OR COALESCE(m.matched_items::jsonb,'[]') <> '[]'::jsonb
                OR COALESCE(m.missing_items::jsonb,'[]') <> '[]'::jsonb
                OR COALESCE(m.uncertain_items::jsonb,'[]') <> '[]'::jsonb
                OR COALESCE(m.evidence::jsonb,'[]') <> '[]'::jsonb
                OR COALESCE(m.risk_flags::jsonb,'[]') <> '[]'::jsonb
                OR COALESCE(m.citations::jsonb,'[]') <> '[]'::jsonb
                OR COALESCE(m.interview_questions::jsonb,'[]') <> '[]'::jsonb
                OR EXISTS (SELECT 1 FROM feedback f WHERE f.match_result_id=m.id AND f.reason IS NOT NULL)
            )
        )
    """)
    ):
        raise RuntimeError("knowledge_privacy_migration_requires_operator_resolution: see Artifact privacy ADR")
    for table, checksum, constraint in (
        ("resumes", "sha256", "ck_resume_lifecycle"),
        ("knowledge_documents", "checksum", "ck_knowledge_lifecycle"),
    ):
        op.add_column(table, sa.Column("lifecycle_status", sa.String(10), server_default="active", nullable=False))
        op.alter_column(table, checksum, nullable=True)
        op.alter_column(table, "original_filename", nullable=True)
        op.create_check_constraint(constraint, table, "lifecycle_status IN ('active', 'deleted')")
    op.add_column("knowledge_documents", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    # Databases already at revision 15 did not necessarily run its final guard.
    # Preserve the deletion decision and sanitize every legacy derivative here.
    connection.execute(
        sa.text("""
        UPDATE resumes SET lifecycle_status='deleted', status='FAILED',
            sha256=NULL, original_filename=NULL, size_bytes=0, uploaded_by=NULL,
            profile='{}', extracted_text=NULL, error_code=NULL, error_message=NULL,
            search_index_status='deleted', search_index_error_code=NULL, search_indexed_at=NULL,
            deleted_at=COALESCE(deleted_at, CURRENT_TIMESTAMP)
        WHERE status='DELETED' OR deleted_at IS NOT NULL
    """)
    )
    connection.execute(
        sa.text("""
        UPDATE knowledge_documents SET lifecycle_status='deleted', status='failed',
            checksum=NULL, original_filename=NULL, size_bytes=0, error_code=NULL, error_message=NULL,
            search_index_status='deleted', search_index_error_code=NULL, search_indexed_at=NULL,
            deleted_at=CURRENT_TIMESTAMP WHERE status='deleted'
    """)
    )
    connection.execute(
        sa.text("""
        DELETE FROM recruiting_chunks c WHERE EXISTS (
            SELECT 1 FROM resumes r WHERE r.tenant_id=c.tenant_id AND r.id=c.source_id
                AND c.source_type='resume' AND r.lifecycle_status='deleted'
        ) OR EXISTS (
            SELECT 1 FROM knowledge_documents d WHERE d.tenant_id=c.tenant_id AND d.id=c.source_id
                AND c.source_type='knowledge_document' AND d.lifecycle_status='deleted'
        )
    """)
    )
    connection.execute(
        sa.text("""
        UPDATE match_results SET dimension_scores='{}', matched_items='[]', missing_items='[]',
            uncertain_items='[]', evidence='[]', risk_flags='[]', summary=NULL, citations='[]',
            grounded_explanation='{}', interview_questions='[]', grounding_status='privacy_redacted',
            fallback_reason=NULL
        WHERE run_id IN (
            SELECT m.id FROM match_runs m JOIN resumes r ON r.id=m.resume_id AND r.tenant_id=m.tenant_id
            WHERE r.lifecycle_status='deleted' OR EXISTS (
                SELECT 1 FROM knowledge_documents d WHERE d.tenant_id=m.tenant_id AND d.lifecycle_status='deleted'
            )
        )
    """)
    )
    connection.execute(
        sa.text("""
        UPDATE feedback SET reason=NULL WHERE match_result_id IN (
            SELECT id FROM match_results WHERE grounding_status='privacy_redacted'
        )
    """)
    )
    deleted_anchor = """
        (EXISTS (SELECT 1 FROM resumes r WHERE r.tenant_id=a.tenant_id AND r.id=a.owner_id
            AND r.artifact_id=a.id AND a.owner_type='resume' AND r.lifecycle_status='deleted')
        OR EXISTS (SELECT 1 FROM knowledge_documents d WHERE d.tenant_id=a.tenant_id AND d.id=a.owner_id
            AND d.artifact_id=a.id AND a.owner_type='knowledge_document' AND d.lifecycle_status='deleted'))
    """
    connection.execute(
        sa.text(
            "UPDATE artifacts a SET status='FAILED',error_code='storage_unavailable' "
            "WHERE a.status='PENDING' AND " + deleted_anchor
        )
    )
    connection.execute(
        sa.text(
            "UPDATE artifacts a SET sha256=NULL,status='CLEANUP_PENDING',error_code=NULL "
            "WHERE a.status IN ('AVAILABLE','FAILED','CLEANUP_FAILED') AND " + deleted_anchor
        )
    )
    # Existing DELETED errors describe actual failed late-object observations;
    # a schema migration has not performed a successful Delete and preserves them.


def downgrade() -> None:
    raise RuntimeError("source_privacy_downgrade_unsupported: erased private content cannot be reconstructed")
