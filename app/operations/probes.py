"""Read-only real probes with bounded driver waits and per-check resource cleanup."""

from pathlib import Path
import re

import boto3  # type: ignore[import-untyped]
from botocore.config import Config as S3Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]
import redis
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, DisconnectionError, TimeoutError as PoolTimeoutError, ProgrammingError
from sqlalchemy.pool import NullPool
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.artifacts.s3 import S3Settings, BUCKET
from app.operations.health import HealthService
from app.operations.heartbeats import OperationsHeartbeats, redis_client


def code_heads() -> set[str]:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "alembic"))
    return set(ScriptDirectory.from_config(config).get_heads())


class PostgreSQLProbe:
    def __init__(self, url):
        self.url = url
        self.heads = code_heads()

    def check(self) -> dict[str, str]:
        result = {"database": "unavailable", "schema": "unavailable", "vector": "unavailable"}
        engine = create_engine(
            self.url,
            poolclass=NullPool,
            connect_args={
                "connect_timeout": 2,
                "options": "-c statement_timeout=500 -c lock_timeout=500 -c default_transaction_read_only=on",
                "keepalives_idle": 1,
                "keepalives_interval": 1,
                "keepalives_count": 1,
                "tcp_user_timeout": 1000,
            },
        )
        try:
            with engine.connect() as connection:
                if connection.scalar(text("SELECT 1")) == 1:
                    result["database"] = "ok"
                version = connection.scalar(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))
                parsed = re.fullmatch(r"(\d+)\.(\d+)(?:\.\d+)?", version or "")
                result["vector"] = "ok" if parsed and tuple(map(int, parsed.groups())) >= (0, 8) else "incompatible"
                installed = set(connection.scalars(text("SELECT version_num FROM alembic_version")))
                result["schema"] = "ok" if installed == self.heads else "incompatible"
        except ProgrammingError as error:
            if getattr(error.orig, "sqlstate", None) != "42P01":
                raise
            result["schema"] = "incompatible"
        except (OperationalError, DisconnectionError, PoolTimeoutError):
            result["database"] = "unavailable"
        finally:
            engine.dispose()
        return result


class RedisProbe:
    def __init__(self, url):
        self.url = url

    def check(self) -> str:
        try:
            with redis_client(self.url) as client:
                return "ok" if client.ping() else "unavailable"
        except redis.RedisError:
            return "unavailable"


class BucketProbe:
    def __init__(self, settings: S3Settings):
        self.settings = settings

    def check(self) -> str:
        client = boto3.client(
            "s3",
            endpoint_url=self.settings.endpoint_url,
            aws_access_key_id=self.settings.access_key_id,
            aws_secret_access_key=self.settings.secret_access_key,
            region_name="us-east-1",
            config=S3Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=0.5,
                read_timeout=0.5,
                retries={"mode": "standard", "total_max_attempts": 1},
            ),
        )
        try:
            client.head_bucket(Bucket=BUCKET)
            return "ok"
        except (BotoCoreError, ClientError, OSError):
            return "unavailable"
        finally:
            client.close()


def build_health(settings) -> HealthService:
    return HealthService(
        PostgreSQLProbe(settings.database_url),
        RedisProbe(settings.celery_broker_url),
        BucketProbe(S3Settings.from_env()),
        OperationsHeartbeats(settings.celery_broker_url),
        ai_enabled=settings.ai_enabled,
        ai_configured=bool(settings.api_key),
    )
