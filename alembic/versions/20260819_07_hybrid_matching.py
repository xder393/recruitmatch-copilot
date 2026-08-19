"""Persist bounded hybrid matching components and citations."""
from alembic import op
import sqlalchemy as sa

revision = "20260819_07"
down_revision = "20260819_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("match_results", sa.Column("rule_score", sa.Float(), nullable=True))
    op.add_column("match_results", sa.Column("semantic_score", sa.Float(), nullable=True))
    op.add_column("match_results", sa.Column("grounding_status", sa.String(50), nullable=True))
    op.add_column("match_results", sa.Column("fallback_reason", sa.String(100), nullable=True))
    op.add_column("match_results", sa.Column("citations", sa.JSON(), nullable=True))
    op.create_index(
        "ix_match_results_run_grounding",
        "match_results",
        ["run_id", "grounding_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_match_results_run_grounding", table_name="match_results")
    op.drop_column("match_results", "citations")
    op.drop_column("match_results", "fallback_reason")
    op.drop_column("match_results", "grounding_status")
    op.drop_column("match_results", "semantic_score")
    op.drop_column("match_results", "rule_score")
