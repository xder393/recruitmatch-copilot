"""Knowledge chunk contracts shared by ingestion and retrieval."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class ChunkInput(BaseModel):
    source_type: str
    content: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    page: Optional[int] = Field(default=None, ge=1)
    vector: List[float] = Field(default_factory=list)
