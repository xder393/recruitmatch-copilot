"""Add fenced processing ownership without executing or resetting existing work."""

from alembic import op
import sqlalchemy as sa

revision = "20260912_18"
down_revision = "20260911_17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("resumes", "knowledge_documents"):
        op.add_column(table, sa.Column("processing_attempts", sa.Integer(), nullable=False, server_default="0"))
        op.add_column(table, sa.Column("processing_lease_epoch", sa.BigInteger(), nullable=False, server_default="0"))
        op.add_column(table, sa.Column("processing_lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("processing_lease_owner", sa.String(100), nullable=True))
        op.add_column(table, sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True))
        # Existing updated_at is the best available proxy for the latest queue cycle.
        op.execute(sa.text(f"UPDATE {table} SET queued_at = COALESCE(updated_at, created_at)"))
        op.alter_column(table, "queued_at", nullable=False, server_default=sa.func.now())


def downgrade() -> None:
    for table in ("knowledge_documents", "resumes"):
        for column in (
            "queued_at",
            "next_retry_at",
            "processing_lease_owner",
            "processing_lease_expires_at",
            "processing_lease_epoch",
            "processing_attempts",
        ):
            op.drop_column(table, column)
