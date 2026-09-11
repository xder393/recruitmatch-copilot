"""Controlled one-shot maintenance entrypoint. No schedule and no raw identity output.

Run inside the application environment: python -m scripts.reconcile_artifacts
Optional --orphan-pages performs bounded, report-only bucket inspection.
"""

import argparse
from dataclasses import asdict
import json
import os

from app.artifacts.s3 import S3ArtifactStore, S3Settings
from app.database import create_engine_and_session
from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWorkFactory
from app.services.artifact_reconciliation import ArtifactReconciliationService
from app.tasks.dispatcher import CeleryTaskDispatcher


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=100, choices=range(1, 1001), metavar="1..1000")
    parser.add_argument("--orphan-pages", type=int, default=0, choices=range(11), metavar="0..10")
    args = parser.parse_args()
    engine, sessions = create_engine_and_session(os.environ["DATABASE_URL"])
    try:
        store = S3ArtifactStore(S3Settings.from_env().client())
        service = ArtifactReconciliationService(
            SqlAlchemyUnitOfWorkFactory(sessions), store, store, CeleryTaskDispatcher()
        )
        print(json.dumps(asdict(service.run_once(batch_size=args.batch_size))))
        cursor = None
        for _ in range(args.orphan_pages):
            report, cursor = service.inspect_orphan_page(store, cursor=cursor, limit=args.batch_size)
            print(json.dumps(asdict(report)))
            if cursor is None or report.error_code is not None:
                break
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
