"""Preserve valid Source text and lineage while removing local storage metadata.

Revision ID: 20260911_15
Revises: 20260911_14
"""

import hashlib

from alembic import op
import sqlalchemy as sa

revision = "20260911_15"
down_revision = "20260911_14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    # Every check precedes DDL. No migration fabricates object availability or
    # silently resets existing business data. Inactive Knowledge still owns data.
    if connection.scalar(sa.text("""
        SELECT EXISTS(SELECT 1 FROM resumes WHERE artifact_id IS NULL AND status <> 'DELETED')
            OR EXISTS(SELECT 1 FROM knowledge_documents WHERE artifact_id IS NULL)
    """)):
        raise RuntimeError("artifact_cutover_requires_explicit_reset: legacy local content; see artifact cutover ADR")
    # NULL document linkage is valid for retained Sources, but cannot exempt
    # private chunks (including inactive generations) from deletion preflight.
    if connection.scalar(sa.text("""
        SELECT EXISTS (
            SELECT 1 FROM recruiting_chunks c
            JOIN resumes r ON r.id=c.source_id AND r.tenant_id=c.tenant_id
            WHERE c.source_type='resume' AND r.status='DELETED'
        )
    """)):
        raise RuntimeError("artifact_cutover_requires_explicit_reset: unsanitized legacy deletion")
    deleted = connection.execute(sa.text("""
        SELECT r.id,r.sha256,r.original_filename,r.size_bytes,r.uploaded_by,r.profile,a.extracted_text
        FROM resumes r LEFT JOIN resume_artifacts a ON a.resume_id=r.id WHERE r.status='DELETED'
    """))
    for row in deleted:
        if (
            row.sha256 != hashlib.sha256(("deleted:" + row.id).encode()).hexdigest()
            or row.original_filename != "deleted" or row.size_bytes != 0 or row.uploaded_by is not None
            or row.profile != {} or row.extracted_text is not None
        ):
            raise RuntimeError("artifact_cutover_requires_explicit_reset: unsanitized legacy deletion")
    invalid = connection.scalar(sa.text("""
        SELECT EXISTS (
            SELECT 1 FROM recruiting_chunks c
            LEFT JOIN resumes r ON r.id=c.source_id AND r.tenant_id=c.tenant_id
            LEFT JOIN resume_artifacts old ON old.id=c.document_id AND old.resume_id=r.id
            WHERE c.source_type='resume' AND c.document_id IS NOT NULL
              AND (r.id IS NULL OR r.artifact_id IS NULL OR r.status='DELETED'
                   OR (old.id IS NULL AND c.document_id <> r.artifact_id))
        ) OR EXISTS (
            SELECT 1 FROM recruiting_chunks c
            LEFT JOIN knowledge_documents d ON d.id=c.source_id AND d.tenant_id=c.tenant_id
            WHERE c.source_type='knowledge_document' AND c.document_id IS NOT NULL
              AND (d.id IS NULL OR c.document_id <> d.id)
        ) OR EXISTS (
            SELECT 1 FROM recruiting_chunks WHERE source_type='job_version' AND document_id IS NOT NULL
        )
    """))
    if invalid:
        raise RuntimeError("artifact_cutover_invalid_lineage")
    op.add_column("resumes", sa.Column("extracted_text", sa.Text(), nullable=True))
    connection.execute(sa.text("""
        UPDATE resumes r SET extracted_text=a.extracted_text FROM resume_artifacts a WHERE a.resume_id=r.id
    """))
    connection.execute(sa.text("""
        UPDATE recruiting_chunks c SET document_id=r.artifact_id
        FROM resumes r JOIN resume_artifacts old ON old.resume_id=r.id
        WHERE c.source_type='resume' AND c.source_id=r.id AND c.tenant_id=r.tenant_id AND c.document_id=old.id
    """))
    op.drop_column("knowledge_documents", "artifact_key")
    op.drop_table("resume_artifacts")


def downgrade() -> None:
    raise RuntimeError("artifact_cutover_downgrade_unsupported: removed local object identities cannot be reconstructed")
