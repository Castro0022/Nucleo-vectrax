#!/usr/bin/env python3
"""
Vectrax Audit Cron Runner
============================
Entry point for cron-triggered audits.

Usage:
  python -m observability.audit_cron --daily
  python -m observability.audit_cron --weekly
  python -m observability.audit_cron --daily --weekly   # both

Crontab (inside container):
  0  6 * * *   /usr/local/bin/python -m observability.audit_cron --daily
  30 6 * * 0   /usr/local/bin/python -m observability.audit_cron --weekly
"""

from __future__ import annotations

import json
import logging
import sys
import os

# Ensure /app is in path when run from cron
sys.path.insert(0, os.environ.get("PYTHONPATH", "/app"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [AUDIT] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def main():
    from observability.audit_engine import run_daily_audit, run_weekly_audit

    args = set(sys.argv[1:])

    if not args or args == {"--help", "-h"}:
        print(__doc__)
        sys.exit(0)

    if "--daily" in args:
        report = run_daily_audit()
        state = report["state"]
        checks = len(report["checks"])
        problems = len(report["problems"])
        print(f"Daily: {state} ({checks} checks, {problems} problems)")

    if "--weekly" in args:
        report = run_weekly_audit()
        state = report["state"]
        passed = report["passed"]
        total = report["total_checks"]
        print(f"Weekly: {state} ({passed}/{total} passed)")


if __name__ == "__main__":
    main()
