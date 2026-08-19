"""Add AI trace metadata and versioned prompt metadata."""
from alembic import op
import sqlalchemy as sa

revision = "20260819_05"
down_revision = "20260819_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("model_traces") as batch:
        batch.add_column(sa.Column("operation", sa.String(100), nullable=True))
        batch.add_column(sa.Column("request_fingerprint", sa.String(64), nullable=True))
        batch.add_column(sa.Column("fallback_reason", sa.String(100), nullable=True))
    op.execute("UPDATE model_traces SET operation = business_type WHERE operation IS NULL")
    op.execute("UPDATE model_traces SET request_fingerprint = '' WHERE request_fingerprint IS NULL")
    with op.batch_alter_table("model_traces") as batch:
        batch.alter_column("operation", nullable=False)
        batch.alter_column("request_fingerprint", nullable=False)
        batch.create_index("ix_model_traces_tenant_created", ["tenant_id", "created_at"])
        batch.create_index("ix_model_traces_tenant_operation_status", ["tenant_id", "operation", "status"])

    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("operation", sa.String(100), nullable=False),
        sa.Column("version", sa.String(100), nullable=False),
        sa.Column("schema_version", sa.String(50), nullable=False),
        sa.Column("template_fingerprint", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "operation", "version", name="uq_prompt_version"),
    )
    op.create_index("ix_prompt_versions_tenant_id", "prompt_versions", ["tenant_id"])
    op.create_index("ix_prompt_versions_operation", "prompt_versions", ["operation"])


def downgrade() -> None:
    op.drop_table("prompt_versions")
    with op.batch_alter_table("model_traces") as batch:
        batch.drop_index("ix_model_traces_tenant_operation_status")
        batch.drop_index("ix_model_traces_tenant_created")
        batch.drop_column("fallback_reason")
        batch.drop_column("request_fingerprint")
        batch.drop_column("operation")
