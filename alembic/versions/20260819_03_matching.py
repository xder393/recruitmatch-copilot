"""Add match runs, explainable results, and feedback."""
from alembic import op
import sqlalchemy as sa

revision = "20260819_03"
down_revision = "20260819_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "match_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resume_id", sa.String(36), sa.ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("algorithm_version", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_match_runs_tenant_id", "match_runs", ["tenant_id"])
    op.create_index("ix_match_runs_resume_id", "match_runs", ["resume_id"])
    op.create_index("ix_match_runs_status", "match_runs", ["status"])
    op.create_table(
        "match_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("match_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_version_id", sa.String(36), sa.ForeignKey("job_versions.id"), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("total_score", sa.Float(), nullable=False),
        sa.Column("dimension_scores", sa.JSON(), nullable=False),
        sa.Column("matched_items", sa.JSON(), nullable=False),
        sa.Column("missing_items", sa.JSON(), nullable=False),
        sa.Column("uncertain_items", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("risk_flags", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "rank", name="uq_match_run_rank"),
    )
    op.create_index("ix_match_results_run_id", "match_results", ["run_id"])
    op.create_index("ix_match_results_job_version_id", "match_results", ["job_version_id"])
    op.create_table(
        "feedback",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "match_result_id",
            sa.String(36),
            sa.ForeignKey("match_results.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("corrected_job_version_id", sa.String(36), sa.ForeignKey("job_versions.id"), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_feedback_tenant_id", "feedback", ["tenant_id"])
    op.create_index("ix_feedback_match_result_id", "feedback", ["match_result_id"])
    op.create_index("ix_feedback_user_id", "feedback", ["user_id"])


def downgrade() -> None:
    op.drop_table("feedback")
    op.drop_table("match_results")
    op.drop_table("match_runs")
