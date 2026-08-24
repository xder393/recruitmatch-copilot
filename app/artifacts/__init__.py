"""Application contracts for recruiting artifacts."""

from app.artifacts.ports import ArtifactLocation, ArtifactStore, ArtifactStoreHealthProbe

__all__ = ["ArtifactLocation", "ArtifactStore", "ArtifactStoreHealthProbe"]
