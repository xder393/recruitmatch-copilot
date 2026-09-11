"""Add independent Artifact lifecycle and nullable Source anchors.

Revision ID: 20260824_13
Revises: 20260824_12
"""

from alembic import op
import sqlalchemy as sa


revision = "20260824_13"
down_revision = "20260824_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "owner_type",
            sa.Enum(
                "resume", "knowledge_document", native_enum=False, create_constraint=True, name="ck_artifact_owner_type"
            ),
            nullable=False,
        ),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("media_type", sa.String(200), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "AVAILABLE",
                "FAILED",
                "CLEANUP_PENDING",
                "CLEANUP_FAILED",
                "DELETED",
                native_enum=False,
                create_constraint=True,
                name="ck_artifact_status",
            ),
            nullable=False,
        ),
        sa.Column(
            "error_code",
            sa.Enum(
                "object_not_found",
                "checksum_mismatch",
                "size_mismatch",
                "size_exceeded",
                "access_denied",
                "storage_unavailable",
                native_enum=False,
                create_constraint=True,
                name="ck_artifact_error_code",
            ),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "owner_id", "id", name="uq_artifact_owner_anchor"),
        sa.CheckConstraint("size_bytes > 0 AND size_bytes <= 10485760", name="ck_artifact_size"),
        sa.CheckConstraint("sha256 IS NULL OR length(sha256) = 64", name="ck_artifact_sha256"),
        sa.CheckConstraint(
            "(status IN ('PENDING', 'AVAILABLE', 'FAILED') AND sha256 IS NOT NULL) OR "
            "(status IN ('CLEANUP_PENDING', 'CLEANUP_FAILED', 'DELETED') AND sha256 IS NULL)",
            name="ck_artifact_checksum_lifecycle",
        ),
    )
    op.create_index(
        "uq_artifact_active_checksum",
        "artifacts",
        ["tenant_id", "owner_type", "sha256"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'AVAILABLE', 'FAILED')"),
    )
    for table in ("resumes", "knowledge_documents"):
        op.add_column(table, sa.Column("artifact_id", sa.String(36), nullable=True))
        op.create_foreign_key(
            "fk_" + table + "_artifact_owner",
            table,
            "artifacts",
            ["tenant_id", "id", "artifact_id"],
            ["tenant_id", "owner_id", "id"],
        )
    op.alter_column("knowledge_documents", "artifact_key", existing_type=sa.String(1000), nullable=True)


def downgrade() -> None:
    # Deliberately refuses rows without a legacy key: never fabricate a backfill.
    op.alter_column("knowledge_documents", "artifact_key", existing_type=sa.String(1000), nullable=False)
    for table in ("resumes", "knowledge_documents"):
        op.drop_constraint("fk_" + table + "_artifact_owner", table, type_="foreignkey")
        op.drop_column(table, "artifact_id")
    op.drop_index("uq_artifact_active_checksum", table_name="artifacts")
    op.drop_table("artifacts")
