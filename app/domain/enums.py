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
