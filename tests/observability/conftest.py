"""Ordinary pytest/CI collection never starts the explicit live acceptance lane."""

import os

collect_ignore = [] if os.environ.get("TELEMETRY_E2E") == "1" else ["test_telemetry_e2e.py"]
