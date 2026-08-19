"""Exact source-evidence validation and sensitive-trait filtering."""
from __future__ import annotations

from app.resumes.schemas import Evidence

_SENSITIVE_TERMS = (
    "性别",
    "年龄",
    "照片",
    "民族",
    "婚姻",
    "婚育",
    "生育",
    "gender",
    "age",
    "ethnicity",
    "marital",
)


def evidence_resolves(source: str, evidence: Evidence | None) -> bool:
    if evidence is None:
        return False
    return 0 <= evidence.start < evidence.end <= len(source) and source[evidence.start : evidence.end] == evidence.text


def contains_sensitive_trait(value: str) -> bool:
    normalized = value.casefold()
    return any(term in normalized for term in _SENSITIVE_TERMS)
