"""Enforce each Source's Artifact owner type at its foreign-key boundary.

Revision ID: 20260911_14
Revises: 20260824_13
"""

from alembic import op
import sqlalchemy as sa


revision = "20260911_14"
down_revision = "20260824_13"
branch_labels = None
depends_on = None

SOURCE_TYPES = {"resumes": "resume", "knowledge_documents": "knowledge_document"}


def upgrade() -> None:
    for table, owner_type in SOURCE_TYPES.items():
        invalid = op.get_bind().scalar(
            sa.text(
                f"SELECT EXISTS (SELECT 1 FROM {table} s JOIN artifacts a ON a.id=s.artifact_id "
                "WHERE a.owner_type <> :owner_type)"
            ),
            {"owner_type": owner_type},
        )
        if invalid:
            # Do not expose file identifiers or silently substitute another file.
            raise RuntimeError("artifact_owner_type_mismatch: " + table)

    for table in SOURCE_TYPES:
        op.drop_constraint("fk_" + table + "_artifact_owner", table, type_="foreignkey")
    op.drop_constraint("uq_artifact_owner_anchor", "artifacts", type_="unique")
    op.create_unique_constraint("uq_artifact_owner_anchor", "artifacts", ["tenant_id", "owner_type", "owner_id", "id"])
    for table, owner_type in SOURCE_TYPES.items():
        op.add_column(
            table,
            sa.Column(
                "artifact_owner_type",
                sa.String(18),
                sa.Computed("'" + owner_type + "'", persisted=True),
                nullable=False,
            ),
        )
        op.create_foreign_key(
            "fk_" + table + "_artifact_owner",
            table,
            "artifacts",
            ["tenant_id", "artifact_owner_type", "id", "artifact_id"],
            ["tenant_id", "owner_type", "owner_id", "id"],
        )


def downgrade() -> None:
    for table in SOURCE_TYPES:
        op.drop_constraint("fk_" + table + "_artifact_owner", table, type_="foreignkey")
        op.drop_column(table, "artifact_owner_type")
    op.drop_constraint("uq_artifact_owner_anchor", "artifacts", type_="unique")
    op.create_unique_constraint("uq_artifact_owner_anchor", "artifacts", ["tenant_id", "owner_id", "id"])
    for table in SOURCE_TYPES:
        op.create_foreign_key(
            "fk_" + table + "_artifact_owner",
            table,
            "artifacts",
            ["tenant_id", "id", "artifact_id"],
            ["tenant_id", "owner_id", "id"],
        )
