"""Build bounded model evidence and reject citations outside the retrieved set."""

from __future__ import annotations

from typing import Iterable

from app.retrieval import RetrievedChunk

_ALLOWED_SOURCE_TYPES = {"resume", "job_version", "knowledge_document"}


def authorized_hits(
    hits: Iterable[RetrievedChunk],
    resume_id: str,
    job_version_id: str,
) -> list[RetrievedChunk]:
    """Keep only the selected resume/JD plus already tenant-filtered knowledge."""
    allowed = []
    for hit in hits:
        if hit.source_type not in _ALLOWED_SOURCE_TYPES:
            continue
        if hit.source_type == "resume" and hit.source_id != resume_id:
            continue
        if hit.source_type == "job_version" and hit.source_id != job_version_id:
            continue
        allowed.append(hit)
    return allowed


def format_evidence(hits: Iterable[RetrievedChunk], max_characters: int) -> str:
    """Render citation-addressable evidence without exceeding the prompt cap."""
    evidence, _ = format_evidence_with_citation_ids(hits, max_characters)
    return evidence


def format_evidence_with_citation_ids(
    hits: Iterable[RetrievedChunk],
    max_characters: int,
) -> tuple[str, frozenset[str]]:
    """Render evidence and structurally track only the chunk markers it adds."""
    if max_characters <= 0:
        return "", frozenset()
    parts: list[str] = []
    citation_ids: set[str] = set()
    remaining = max_characters
    for hit in hits:
        prefix = f"[citation:{hit.citation_id}] "
        if len(prefix) >= remaining:
            break
        content = hit.content[: remaining - len(prefix)]
        part = prefix + content
        parts.append(part)
        citation_ids.add(hit.citation_id)
        remaining -= len(part)
        if remaining <= 1:
            break
        remaining -= 1
    return "\n".join(parts), frozenset(citation_ids)


def citations_are_known(citation_ids: list[str], known_ids: set[str]) -> bool:
    return bool(citation_ids) and all(item in known_ids for item in citation_ids)
