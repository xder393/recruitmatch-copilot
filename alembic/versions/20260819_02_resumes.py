"""Add tenant-isolated resume lifecycle tables."""
from alembic import op
import sqlalchemy as sa

revision = "20260819_02"
down_revision = "20260819_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resumes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uploaded_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("media_type", sa.String(200), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("profile", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "sha256", name="uq_resume_tenant_hash"),
    )
    op.create_index("ix_resumes_tenant_id", "resumes", ["tenant_id"])
    op.create_index("ix_resumes_status", "resumes", ["status"])
    op.create_table(
        "resume_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("resume_id", sa.String(36), sa.ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("resume_id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_resume_artifacts_resume_id", "resume_artifacts", ["resume_id"], unique=True)


def downgrade() -> None:
    op.drop_table("resume_artifacts")
    op.drop_table("resumes")
