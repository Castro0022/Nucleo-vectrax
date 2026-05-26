"""
core/observability/router_telemetry.py — Telemetría real del SmartRouter.

Cada decisión de routing se persiste en un JSONL rotativo.
No almacena contenido de mensajes — solo métricas abstractas.

Persistencia: vault/router_telemetry.jsonl (últimas 1000 líneas, auto-rotación).
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.router_telemetry")

_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "vault", "router_telemetry.jsonl",
)
_MAX_LINES = 1000


def record_decision(
    user_id: str,
    intent: str,
    strategy: str,
    topic: str,
    confidence: float,
    risk: str,
    memory_depth: int,
    latency_ms: float,
    classification_method: str = "",
    reason: str = "",
) -> None:
    """Append one routing decision to the telemetry log.

    NEVER stores message content, credentials, or personal data.
    """
    entry = {
        "ts": time.time(),
        "user": user_id[:20] if user_id else "",
        "intent": intent,
        "strategy": strategy,
        "topic": topic,
        "conf": round(confidence, 2),
        "risk": risk,
        "mem_depth": memory_depth,
        "latency_ms": round(latency_ms, 1),
        "method": classification_method,
        "reason": reason[:80],
    }
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        with open(_LOG_PATH, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _rotate_if_needed()
    except Exception as exc:
        logger.debug("telemetry write failed: %s", exc)


def _rotate_if_needed() -> None:
    """Keep only the last _MAX_LINES entries."""
    try:
        with open(_LOG_PATH, "r") as f:
            lines = f.readlines()
        if len(lines) > _MAX_LINES * 1.2:
            with open(_LOG_PATH, "w") as f:
                f.writelines(lines[-_MAX_LINES:])
    except Exception:
        pass


def get_recent(n: int = 50) -> List[Dict[str, Any]]:
    """Read the last N telemetry entries."""
    try:
        with open(_LOG_PATH, "r") as f:
            lines = f.readlines()
        entries = []
        for line in lines[-n:]:
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
        return entries
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Periodic digest — runs from pipeline_worker every 6h
# ---------------------------------------------------------------------------

_DIGEST_INTERVAL = 6 * 3600  # 6 hours
_ALERT_THRESHOLDS = {
    "low_depth_pct": 50,     # alert if >50% of routes are low-depth
    "avg_latency_ms": 5000,  # alert if avg latency >5s
    "fallback_pct": 40,      # alert if >40% go to fallback strategies
}


def run_digest(tg_send_fn) -> bool:
    """Generate and send a telemetry digest to the creator.

    Called from pipeline_worker every _DIGEST_INTERVAL.
    Only sends if there are >=10 entries since last digest.
    Returns True if a digest was sent.
    """
    entries = get_recent(200)
    if len(entries) < 10:
        return False

    total = len(entries)
    strategies = {}
    depths = []
    latencies = []
    fallback_count = 0
    low_depth_count = 0

    for e in entries:
        s = e.get("strategy", "")
        strategies[s] = strategies.get(s, 0) + 1
        d = e.get("mem_depth", 0)
        depths.append(d)
        latencies.append(e.get("latency_ms", 0))
        if d < 5:
            low_depth_count += 1
        if s in ("resolve_online", "route_single") and e.get("intent") == "memory":
            fallback_count += 1

    avg_depth = sum(depths) / max(1, len(depths))
    avg_lat = sum(latencies) / max(1, len(latencies))
    low_pct = low_depth_count / max(1, total) * 100
    fallback_pct = fallback_count / max(1, total) * 100

    # Build digest
    lines = ["\U0001f4ca Router Digest (%d decisions)" % total, ""]

    # Strategy distribution
    for s, c in sorted(strategies.items(), key=lambda x: -x[1]):
        lines.append("  %s: %d (%.0f%%)" % (s, c, c / total * 100))

    lines.append("")
    lines.append("Memory: avg_depth=%.1f | low(<5)=%d (%.0f%%)" % (avg_depth, low_depth_count, low_pct))
    lines.append("Latency: avg=%.0fms" % avg_lat)

    # Alerts
    alerts = []
    if low_pct > _ALERT_THRESHOLDS["low_depth_pct"]:
        alerts.append("\u26a0 %d%% users have shallow memory (<5 interactions)" % int(low_pct))
    if avg_lat > _ALERT_THRESHOLDS["avg_latency_ms"]:
        alerts.append("\u26a0 Avg latency %.0fms exceeds threshold" % avg_lat)
    if fallback_pct > _ALERT_THRESHOLDS["fallback_pct"]:
        alerts.append("\u26a0 %.0f%% memory\u2192fallback rate" % fallback_pct)

    if alerts:
        lines.append("")
        for a in alerts:
            lines.append(a)
    else:
        lines.append("")
        lines.append("\u2705 No alerts")

    # Send to creator
    import os
    creator_uid = os.environ.get("VX_CREATOR_ID", "2030762343")
    try:
        tg_send_fn(int(creator_uid), "\n".join(lines))
        logger.info("Router digest sent: %d entries, %d alerts", total, len(alerts))
        return True
    except Exception as exc:
        logger.debug("Router digest send failed: %s", exc)
        return False


def build_summary(n: int = 100) -> str:
    """Build a human-readable summary of the last N routing decisions."""
    entries = get_recent(n)
    if not entries:
        return "Sin telemetría de routing disponible."

    total = len(entries)
    strategies = {}
    topics = {}
    intents = {}
    depths = []
    latencies = []
    low_depth_routes = 0

    for e in entries:
        s = e.get("strategy", "?")
        strategies[s] = strategies.get(s, 0) + 1
        t = e.get("topic", "?")
        topics[t] = topics.get(t, 0) + 1
        i = e.get("intent", "?")
        intents[i] = intents.get(i, 0) + 1
        d = e.get("mem_depth", 0)
        depths.append(d)
        latencies.append(e.get("latency_ms", 0))
        if d < 5:
            low_depth_routes += 1

    avg_depth = sum(depths) / max(1, len(depths))
    avg_lat = sum(latencies) / max(1, len(latencies))

    lines = [
        "📊 Router Telemetry — last %d decisions" % total,
        "",
        "Strategies:",
    ]
    for s, c in sorted(strategies.items(), key=lambda x: -x[1]):
        pct = c / total * 100
        lines.append("  %s: %d (%.0f%%)" % (s, c, pct))

    lines.append("")
    lines.append("Topics:")
    for t, c in sorted(topics.items(), key=lambda x: -x[1])[:5]:
        lines.append("  %s: %d" % (t, c))

    lines.append("")
    lines.append("Memory depth: avg=%.1f | low_depth(<5)=%d/%d (%.0f%%)" % (
        avg_depth, low_depth_routes, total,
        low_depth_routes / max(1, total) * 100,
    ))
    lines.append("Latency: avg=%.0fms" % avg_lat)

    # Last 5 entries
    lines.append("")
    lines.append("Last 5:")
    for e in entries[-5:]:
        ts = time.strftime("%H:%M", time.localtime(e.get("ts", 0)))
        lines.append("  %s %s → %s (d=%d, conf=%.2f)" % (
            ts, e.get("intent", "?"), e.get("strategy", "?"),
            e.get("mem_depth", 0), e.get("conf", 0),
        ))

    return "\n".join(lines)
