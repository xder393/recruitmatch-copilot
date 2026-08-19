"""Track repairable resume and job-version search indexing state."""
from alembic import op
import sqlalchemy as sa

revision = "20260819_09"
down_revision = "20260819_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("resumes", "job_versions"):
        op.add_column(
            table,
            sa.Column("search_index_status", sa.String(30), server_default="pending", nullable=False),
        )
        op.add_column(table, sa.Column("search_index_error", sa.String(100), nullable=True))
        op.add_column(table, sa.Column("search_indexed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for table in ("job_versions", "resumes"):
        op.drop_column(table, "search_indexed_at")
        op.drop_column(table, "search_index_error")
        op.drop_column(table, "search_index_status")
