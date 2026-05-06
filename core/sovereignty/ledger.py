"""
core/sovereignty/ledger.py — Audit log inmutable de cada decisión de envío.

Cada call a require_authorization() persiste un registro:
  ts, chat_id, user_id, reason, status, rule, notes

Permite responder, para millones de users:
  - ¿Cuántos envíos ALLOWED / BLOCKED / OBSERVED en las últimas 24h?
  - ¿Qué reasons disparan más bloqueos?
  - ¿Qué users reciben más mensajes (por reason)?
  - ¿Algún reason está siendo abusado?

Backend: append-only JSONL en /var/log/vectrax/sovereignty.jsonl.
Append-only para garantizar inmutabilidad y baja contención.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import Counter
from typing import Any, Dict, List

from core.sovereignty.policy import Decision, DecisionStatus

logger = logging.getLogger("vectrax.sovereignty.ledger")

_LEDGER_PATH = os.environ.get(
    "VECTRAX_SOVEREIGNTY_LEDGER",
    os.path.join(
        os.path.expanduser("~"),
        ".vectrax", "sovereignty.jsonl",
    ),
)

_lock = threading.Lock()


def record_decision(decision: Decision, payload_size: int = 0) -> None:
    """Append una decisión al ledger. NUNCA raise.

    `payload_size` es opcional (chars del texto). Útil para análisis de
    volumen sin guardar el contenido (privacy).
    """
    try:
        os.makedirs(os.path.dirname(_LEDGER_PATH), exist_ok=True)
        entry = {
            "ts": time.time(),
            "ts_human": time.strftime(
                "%Y-%m-%d %H:%M:%S UTC", time.gmtime()
            ),
            "status": decision.status.value,
            "reason": decision.reason.value,
            "chat_id": decision.chat_id,
            "user_id": decision.user_id,
            "rule": decision.rule,
            "notes": decision.notes,
            "payload_size": payload_size,
        }
        with _lock:
            with open(_LEDGER_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug("ledger record failed: %s", exc)


def recent_decisions(n: int = 50) -> List[Dict[str, Any]]:
    """Lee las últimas n entradas del ledger. Solo lectura."""
    try:
        if not os.path.exists(_LEDGER_PATH):
            return []
        with _lock:
            with open(_LEDGER_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
        recent = lines[-n:] if len(lines) > n else lines
        out = []
        for line in recent:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
    except Exception:
        return []


def audit_stats(window_seconds: int = 86400) -> Dict[str, Any]:
    """Agrega estadísticas de los últimos `window_seconds` segundos.

    Returns:
        {
          "total": int,
          "by_status": {"ALLOWED": int, "BLOCKED": int, "OBSERVED": int},
          "by_reason": {<reason>: int, ...},
          "blocked_by_reason": {<reason>: int, ...},
          "top_users_blocked": [(user_id, count), ...],
        }
    """
    cutoff = time.time() - window_seconds
    by_status: Counter = Counter()
    by_reason: Counter = Counter()
    blocked_by_reason: Counter = Counter()
    blocked_users: Counter = Counter()
    total = 0

    try:
        if not os.path.exists(_LEDGER_PATH):
            return {
                "total": 0, "by_status": {}, "by_reason": {},
                "blocked_by_reason": {}, "top_users_blocked": [],
            }
        with _lock:
            with open(_LEDGER_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if float(e.get("ts", 0)) < cutoff:
                        continue
                    total += 1
                    status = e.get("status", "UNKNOWN")
                    reason = e.get("reason", "unknown")
                    by_status[status] += 1
                    by_reason[reason] += 1
                    if status in ("BLOCKED", "OBSERVED"):
                        blocked_by_reason[reason] += 1
                        u = e.get("user_id") or "anon"
                        blocked_users[u] += 1
    except Exception:
        pass

    return {
        "total": total,
        "by_status": dict(by_status),
        "by_reason": dict(by_reason),
        "blocked_by_reason": dict(blocked_by_reason),
        "top_users_blocked": blocked_users.most_common(10),
    }
