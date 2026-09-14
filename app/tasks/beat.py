"""Singleton Celery Beat scheduler plus the Worker's operational heartbeat bootstep."""

import logging
from threading import Lock
from time import monotonic
from uuid import uuid4

from celery import bootsteps  # type: ignore[import-untyped]
from celery.beat import Scheduler  # type: ignore[import-untyped]
from kombu.exceptions import OperationalError as BrokerError  # type: ignore[import-untyped]
import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.operations.heartbeats import OperationsHeartbeats
from app.processing.recovery import RecoveryScanner, RecoveryDispatchUnavailable
from app.processing.recovery_repository import RecoveryRepository
from app.repositories.ports import PersistenceUnavailable
from app.tasks.dispatcher import CeleryTaskDispatcher

logger = logging.getLogger(__name__)


class RecoveryScheduler(Scheduler):
    """tick executes bounded SQL and broker I/O in the Beat process itself."""

    def __init__(self, *args, scanner=None, heartbeats=None, maintenance_dispatch=None, clock=monotonic, **kwargs):
        self.scanner = scanner
        self.heartbeats = heartbeats
        self.maintenance_dispatch = maintenance_dispatch
        self.clock = clock
        self.next_maintenance = 0.0
        self.engine = None
        super().__init__(*args, **kwargs)

    def _compose(self):
        settings = Settings.load()
        if self.scanner is None:
            self.engine = create_engine(
                settings.database_url,
                poolclass=NullPool,
                connect_args={
                    "connect_timeout": 2,
                    "options": "-c statement_timeout=1000 -c lock_timeout=500",
                    "keepalives_idle": 1,
                    "keepalives_interval": 1,
                    "keepalives_count": 1,
                    "tcp_user_timeout": 1000,
                },
            )
            self.scanner = RecoveryScanner(
                RecoveryRepository(sessionmaker(self.engine), max_attempts=settings.processing_max_attempts),
                CeleryTaskDispatcher(),
            )
        if self.heartbeats is None:
            self.heartbeats = OperationsHeartbeats(settings.celery_broker_url)
        if self.maintenance_dispatch is None:
            self.maintenance_dispatch = self._dispatch_maintenance

    def _dispatch_maintenance(self):
        try:
            self.app.send_task(
                "recruitmatch.reconcile_artifacts", args=(), expires=60, argsrepr="[redacted]", kwargsrepr="[redacted]"
            )
        except (BrokerError, redis.RedisError, OSError):
            raise RecoveryDispatchUnavailable() from None

    def tick(self, **kwargs):
        self._compose()
        try:
            self.scanner.run_once()
        except PersistenceUnavailable:
            logger.warning("recovery_database_unavailable")
            return 10
        now = self.clock()
        if now >= self.next_maintenance:
            self.next_maintenance = now + 60
            try:
                self.maintenance_dispatch()
            except RecoveryDispatchUnavailable:
                logger.warning("maintenance_dispatch_unavailable")
        try:
            self.heartbeats.pulse_beat()
        except redis.RedisError:
            logger.warning("heartbeat_unavailable")
        return 10

    def close(self):
        if self.engine is not None:
            self.engine.dispose()
        super().close()


class WorkerHeartbeatStep(bootsteps.StartStopStep):
    requires = {"celery.worker.components:Timer"}

    def __init__(self, worker, **kwargs):
        self.identity = uuid4().hex
        self.heartbeats = OperationsHeartbeats(Settings.load().celery_broker_url)
        self.lock = Lock()
        self.pulsing = False
        self.timer = None

    def _pulse(self):
        with self.lock:
            if self.pulsing:
                try:
                    self.heartbeats.pulse_worker(self.identity)
                except redis.RedisError:
                    logger.warning("heartbeat_unavailable")

    def start(self, worker):
        self.pulsing = True
        self._pulse()
        self.timer = worker.timer.call_repeatedly(10, self._pulse)

    def stop(self, worker):
        with self.lock:
            self.pulsing = False
            if self.timer is not None:
                self.timer.cancel()
            try:
                self.heartbeats.remove_worker(self.identity)
            except redis.RedisError:
                logger.warning("heartbeat_unavailable")

    terminate = stop
