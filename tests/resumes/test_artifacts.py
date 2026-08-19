from __future__ import annotations


def test_storage_key_never_contains_caller_filename(tmp_path):
    """Catches path traversal and filename-based object collisions."""
    from app.resumes.artifacts import LocalArtifactStore

    store = LocalArtifactStore(tmp_path)
    stored = store.put("tenant-acme", "../../secret.txt", b"Python engineer")

    assert "secret.txt" not in stored.key
    assert ".." not in stored.key
    assert store.read(stored.key) == b"Python engineer"


def test_artifact_delete_removes_only_exact_key(tmp_path):
    """Catches broad cleanup deleting another resume artifact."""
    from app.resumes.artifacts import LocalArtifactStore

    store = LocalArtifactStore(tmp_path)
    first = store.put("tenant", "a.txt", b"first")
    second = store.put("tenant", "b.txt", b"second")
    store.delete(first.key)

    assert store.read(second.key) == b"second"
