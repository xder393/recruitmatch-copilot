"""Durable fair storage maintenance progress, independent of processing.

Revision ID: 20260911_17
Revises: 20260911_16
"""

from alembic import op
import sqlalchemy as sa

revision = "20260911_17"
down_revision = "20260911_16"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifact_maintenance_cursors",
        sa.Column("scope", sa.String(36), primary_key=True),
        sa.Column("lane", sa.String(10), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
        sa.Column("after_id", sa.String(36), nullable=True),
        sa.CheckConstraint(
            "(scope = 'global' AND lane = 'tenants' AND tenant_id IS NULL) OR "
            "(scope = tenant_id AND tenant_id IS NOT NULL AND lane IN ('pending', 'cleanup', 'deleted'))",
            name="ck_artifact_maintenance_scope_lane",
        ),
    )


def downgrade() -> None:
    op.drop_table("artifact_maintenance_cursors")
