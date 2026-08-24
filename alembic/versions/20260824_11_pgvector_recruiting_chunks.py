"""Add the PostgreSQL-only pgvector recruiting evidence schema."""

from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa

revision = "20260824_11"
down_revision = "20260819_10"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    dialect = op.get_bind().dialect.name
    if dialect != "postgresql":
        raise RuntimeError(
            f"revision 20260824_11 requires PostgreSQL with pgvector; refusing to migrate dialect {dialect!r}"
        )


def upgrade() -> None:
    _require_postgresql()
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    extension_version = (
        op.get_bind().execute(sa.text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")).scalar_one()
    )
    version = tuple(int(part) for part in extension_version.split(".")[:2])
    if version < (0, 8):
        raise RuntimeError(f"pgvector 0.8 or newer is required; found {extension_version}")

    for table in ("resumes", "job_versions"):
        op.alter_column(table, "search_index_error", new_column_name="search_index_error_code")
        op.add_column(
            table,
            sa.Column("active_index_generation", sa.Integer(), server_default="0", nullable=False),
        )

    op.alter_column("knowledge_documents", "active_generation", new_column_name="active_index_generation")
    op.add_column(
        "knowledge_documents",
        sa.Column("search_index_status", sa.String(30), server_default="pending", nullable=False),
    )
    op.add_column("knowledge_documents", sa.Column("search_index_error_code", sa.String(100), nullable=True))
    op.add_column("knowledge_documents", sa.Column("search_indexed_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "recruiting_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=True),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_id", sa.String(100), nullable=False),
        sa.Column("source_version", sa.String(100), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("citation_id", sa.String(200), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("section", sa.String(200), nullable=True),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(512), nullable=False),
        sa.Column("embedding_model", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('resume', 'job_version', 'knowledge_document')",
            name="ck_recruiting_chunk_source_type",
        ),
        sa.CheckConstraint("generation > 0", name="ck_recruiting_chunk_generation_positive"),
        sa.CheckConstraint("start_offset >= 0 AND end_offset > start_offset", name="ck_recruiting_chunk_offsets"),
        sa.UniqueConstraint("tenant_id", "citation_id", name="uq_recruiting_chunk_tenant_citation"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_type",
            "source_id",
            "source_version",
            "generation",
            "start_offset",
            "end_offset",
            name="uq_recruiting_chunk_source_generation_offsets",
        ),
    )
    op.create_index(
        "ix_recruiting_chunk_tenant_type_active",
        "recruiting_chunks",
        ["tenant_id", "source_type", "is_active"],
    )
    op.create_index(
        "ix_recruiting_chunk_source_active",
        "recruiting_chunks",
        ["tenant_id", "source_type", "source_id", "source_version", "is_active"],
    )
    op.create_index(
        "ix_recruiting_chunk_document_generation_active",
        "recruiting_chunks",
        ["tenant_id", "document_id", "generation", "is_active"],
        postgresql_where=sa.text("document_id IS NOT NULL"),
    )
    op.create_index(
        "ix_recruiting_chunk_embedding_hnsw_active",
        "recruiting_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_where=sa.text("is_active = true"),
    )


def downgrade() -> None:
    _require_postgresql()
    op.drop_table("recruiting_chunks")

    op.drop_column("knowledge_documents", "search_indexed_at")
    op.drop_column("knowledge_documents", "search_index_error_code")
    op.drop_column("knowledge_documents", "search_index_status")
    op.alter_column("knowledge_documents", "active_index_generation", new_column_name="active_generation")

    for table in ("job_versions", "resumes"):
        op.drop_column(table, "active_index_generation")
        op.alter_column(table, "search_index_error_code", new_column_name="search_index_error")
