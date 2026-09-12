"""Bounded retry policy shared by workers and PostgreSQL recovery transitions."""

from dataclasses import dataclass


TRANSIENT_PROCESSING_CODES = frozenset({"storage_unavailable", "embedding_failed", "processing_timeout"})


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    short_seconds: int = 30
    long_seconds: int = 300

    def __post_init__(self):
        if (
            any(
                type(value) is not int or value <= 0
                for value in (self.max_attempts, self.short_seconds, self.long_seconds)
            )
            or self.long_seconds <= self.short_seconds
        ):
            raise ValueError("processing_retry_policy_invalid")
