"""Build bounded model evidence and reject citations outside the retrieved set."""
from __future__ import annotations

from typing import Iterable

from app.knowledge.index import RetrievedChunk


def authorized_hits(
    hits: Iterable[RetrievedChunk],
    resume_id: str,
    job_version_id: str,
) -> list[RetrievedChunk]:
    """Keep only the selected resume/JD plus already tenant-filtered knowledge."""
    allowed = []
    for hit in hits:
        if hit.source_type == "resume" and hit.source_id != resume_id:
            continue
        if hit.source_type == "job" and hit.source_id != job_version_id:
            continue
        allowed.append(hit)
    return allowed


def format_evidence(hits: Iterable[RetrievedChunk], max_characters: int) -> str:
    """Render citation-addressable evidence without exceeding the prompt cap."""
    if max_characters <= 0:
        return ""
    parts: list[str] = []
    remaining = max_characters
    for hit in hits:
        prefix = f"[citation:{hit.citation_id}] "
        if len(prefix) >= remaining:
            break
        content = hit.content[: remaining - len(prefix)]
        part = prefix + content
        parts.append(part)
        remaining -= len(part)
        if remaining <= 1:
            break
        remaining -= 1
    return "\n".join(parts)


def citations_are_known(citation_ids: list[str], known_ids: set[str]) -> bool:
    return bool(citation_ids) and all(item in known_ids for item in citation_ids)
