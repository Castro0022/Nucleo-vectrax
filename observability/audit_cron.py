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
import time

# Ensure /app is in path when run from cron
sys.path.insert(0, os.environ.get("PYTHONPATH", "/app"))

# Load .env so scheduled audits have credentials (Telegram alerts + external
# service checks). audit_engine reads os.environ directly and does NOT call
# load_dotenv itself, so the --loop service must load it here.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [AUDIT] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _loop() -> None:
    """Long-running scheduler (pure Python, macOS-safe).

    Replaces the system `cron -f` service — which gets SIGKILL'd on macOS
    (exit=-9 crash loop) — with an in-process scheduler: a baseline daily
    audit ~30s after startup, then the daily audit at/after 06:00 and the
    weekly audit Sunday >=06:30. Defensive: an audit that raises is logged
    and the loop continues, so the supervised service never crash-loops.
    """
    import datetime as _dt
    from observability.audit_engine import run_daily_audit, run_weekly_audit

    log = logging.getLogger("vectrax.audit")
    log.info("audit scheduler started (--loop)")

    time.sleep(30)  # let the system settle after startup
    try:
        log.info("startup daily audit: %s", run_daily_audit().get("state"))
    except Exception as e:
        log.error("startup daily audit failed: %s", e)

    last_daily = _dt.date.today()
    last_weekly = None
    while True:
        try:
            now = _dt.datetime.now()
            today = now.date()
            if now.hour >= 6 and last_daily != today:
                last_daily = today
                try:
                    log.info("daily audit: %s", run_daily_audit().get("state"))
                except Exception as e:
                    log.error("daily audit failed: %s", e)
            if now.weekday() == 6 and (now.hour, now.minute) >= (6, 30) and last_weekly != today:
                last_weekly = today
                try:
                    log.info("weekly audit: %s", run_weekly_audit().get("state"))
                except Exception as e:
                    log.error("weekly audit failed: %s", e)
        except Exception as e:
            log.error("scheduler loop error: %s", e)
        time.sleep(60)


def main():
    from observability.audit_engine import run_daily_audit, run_weekly_audit

    args = set(sys.argv[1:])

    if not args or args == {"--help", "-h"}:
        print(__doc__)
        sys.exit(0)

    if "--loop" in args:
        _loop()
        return

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
