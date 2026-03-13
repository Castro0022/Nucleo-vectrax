"""
Vectrax Observability — Daily Report
=======================================
Generates a daily health report covering:
  - Core health
  - Agent health
  - Events summary
  - Proposals (created, applied, rejected)
  - Errors
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict

REPORTS_DIR = os.path.join(os.path.expanduser("~/Vectrax"), "reports")


def generate_daily_report() -> Dict[str, Any]:
    """
    Collect platform-wide metrics and produce a daily report.
    Returns the report dict and writes to reports/daily_platform_YYYY-MM-DD.json.
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")

    # Core health
    core_health = _check_core_health()

    # Agent status
    agent_status = _check_agent_status()

    # Event counts (from in-memory store via route accessor)
    events_summary = _summarize_events()

    # Proposal stats from audit ledger
    proposal_stats = _summarize_proposals()

    report = {
        "date": today,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "core": core_health,
        "agent": agent_status,
        "events": events_summary,
        "proposals": proposal_stats,
    }

    # Write to file
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, f"daily_platform_{today}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return report


def _check_core_health() -> Dict[str, Any]:
    """Best-effort core health check."""
    try:
        from core import state_manager
        state = state_manager.load()
        return {
            "status": "ok",
            "governor_mode": state.get("governor_mode", "unknown"),
            "cycles": state.get("cycles", 0),
            "errors_since_boot": state.get("errors_since_boot", 0),
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _check_agent_status() -> Dict[str, Any]:
    """Best-effort agent status."""
    try:
        from agent.config import load_agent_config
        cfg = load_agent_config()
        return {
            "agent_id": cfg.agent_id,
            "mode": cfg.mode,
            "core_url": cfg.core_url,
        }
    except Exception:
        return {"status": "not_configured"}


def _summarize_events() -> Dict[str, Any]:
    """Summarize events from in-memory store."""
    try:
        from services.core.routes.events import get_events
        events = get_events()
        by_type: Dict[str, int] = {}
        for ev in events:
            t = ev.get("event_type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1
        return {"total": len(events), "by_type": by_type}
    except Exception:
        return {"total": 0, "by_type": {}}


def _summarize_proposals() -> Dict[str, Any]:
    """Summarize proposals from audit ledger."""
    try:
        from core.audit_ledger import query
        entries = query(limit=1000, action_filter="proposal.apply")
        approved = sum(1 for e in entries if e.get("decision") == "approved")
        rejected = sum(1 for e in entries if e.get("decision") == "rejected")
        return {
            "total": len(entries),
            "approved": approved,
            "rejected": rejected,
        }
    except Exception:
        return {"total": 0, "approved": 0, "rejected": 0}
