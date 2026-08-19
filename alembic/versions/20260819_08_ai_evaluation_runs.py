"""Store privacy-safe comparative AI evaluation metadata."""
from alembic import op
import sqlalchemy as sa

revision = "20260819_08"
down_revision = "20260819_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_evaluation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
        sa.Column("dataset_version", sa.String(100), nullable=False),
        sa.Column("algorithm_version", sa.String(100), nullable=False),
        sa.Column("model_version", sa.String(200), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("embedding_version", sa.String(200), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "dataset_version", "algorithm_version", "model_version", "prompt_version",
            "embedding_version", name="uq_ai_evaluation_version_set",
        ),
    )
    op.create_index("ix_ai_evaluation_runs_tenant_id", "ai_evaluation_runs", ["tenant_id"])
    op.create_table(
        "ai_evaluation_cases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("ai_evaluation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_key", sa.String(100), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("failure_categories", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.Float(), nullable=False),
    )
    op.create_index("ix_ai_evaluation_cases_run_id", "ai_evaluation_cases", ["run_id"])


def downgrade() -> None:
    op.drop_table("ai_evaluation_cases")
    op.drop_table("ai_evaluation_runs")
