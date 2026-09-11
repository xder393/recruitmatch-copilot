"""Independent artifact persistence; no object-store client or key strings."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.domain.artifacts import ArtifactErrorCode, ArtifactOwnerType, ArtifactStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner_type", "owner_id", "id", name="uq_artifact_owner_anchor"),
        CheckConstraint("size_bytes > 0 AND size_bytes <= 10485760", name="ck_artifact_size"),
        CheckConstraint("sha256 IS NULL OR length(sha256) = 64", name="ck_artifact_sha256"),
        CheckConstraint(
            "(status IN ('PENDING', 'AVAILABLE', 'FAILED') AND sha256 IS NOT NULL) OR "
            "(status IN ('CLEANUP_PENDING', 'CLEANUP_FAILED', 'DELETED') AND sha256 IS NULL)",
            name="ck_artifact_checksum_lifecycle",
        ),
        Index(
            "uq_artifact_active_checksum",
            "tenant_id",
            "owner_type",
            "sha256",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'AVAILABLE', 'FAILED')"),
            sqlite_where=text("status IN ('PENDING', 'AVAILABLE', 'FAILED')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    owner_type: Mapped[ArtifactOwnerType] = mapped_column(
        Enum(
            ArtifactOwnerType,
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum: [item.value for item in enum],
            name="ck_artifact_owner_type",
        ),
        nullable=False,
    )
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    media_type: Mapped[str] = mapped_column(String(200), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ArtifactStatus] = mapped_column(
        Enum(
            ArtifactStatus, native_enum=False, create_constraint=True, validate_strings=True, name="ck_artifact_status"
        ),
        default=ArtifactStatus.PENDING,
        nullable=False,
    )
    error_code: Mapped[ArtifactErrorCode | None] = mapped_column(
        Enum(
            ArtifactErrorCode,
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum: [item.value for item in enum],
            name="ck_artifact_error_code",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )
