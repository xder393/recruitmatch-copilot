"""Reserve recovery dispatch separately from processing ownership and retry eligibility."""

from alembic import op
import sqlalchemy as sa

revision = "20260912_19"
down_revision = "20260912_18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("resumes", "knowledge_documents"):
        op.add_column(table, sa.Column("recovery_dispatch_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("recovery_dispatch_token", sa.String(36), nullable=True))
        op.add_column(table, sa.Column("recovery_dispatch_error_code", sa.String(100), nullable=True))
        op.create_index(f"ix_{table}_recovery_dispatch", table, ["recovery_dispatch_at", "tenant_id", "id"])


def downgrade() -> None:
    for table in ("knowledge_documents", "resumes"):
        op.drop_index(f"ix_{table}_recovery_dispatch", table_name=table)
        for column in ("recovery_dispatch_error_code", "recovery_dispatch_token", "recovery_dispatch_at"):
            op.drop_column(table, column)
