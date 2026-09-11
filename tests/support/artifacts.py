"""Synthetic persistent Artifact fixtures shared by SQLite processor tests."""

import hashlib
from io import BytesIO
from uuid import uuid4

from app.repositories.artifacts import ArtifactRepository


def attach_artifact(session, owner, content, *, owner_type="resume", store=None):
    owner.id = owner.id or str(uuid4())
    digest = hashlib.sha256(content).hexdigest()
    repo = ArtifactRepository(session)
    artifact = repo.claim_upload(owner.tenant_id, owner_type, owner.id, digest, owner.media_type, len(content))
    owner.artifact_id = artifact.id
    owner.size_bytes = len(content)
    if owner_type == "resume":
        owner.sha256 = digest
    else:
        owner.checksum = digest
    repo.mark_available(owner.tenant_id, artifact.id, owner_type=owner_type, owner_id=owner.id)
    location = repo.resolve_location(owner.tenant_id, owner_type, owner.id, artifact.id)
    if store is not None:
        store.put(location, BytesIO(content), len(content), digest)
    return location
