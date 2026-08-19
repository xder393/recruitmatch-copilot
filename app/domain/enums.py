"""Shared recruiting lifecycle enums."""
from enum import Enum


class Role(str, Enum):
    ADMIN = "admin"
    RECRUITER = "recruiter"
    LEAD = "lead"


class JobStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    INACTIVE = "inactive"


class ResumeStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DELETED = "deleted"
