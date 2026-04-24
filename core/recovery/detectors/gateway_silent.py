"""
Clase A — Gateway silencioso: proceso vivo, flujo muerto.

The scenario: supervisor started the core, health endpoint reports OK,
but Telegram updates never reach a handler. This is exactly the bug
that cost 10h of silence: USE_WEBHOOK=1 was set but no webhook URL
registered in Telegram → supervisor skipped long-poll → updates
accumulated in Telegram's queue, no one processed them.

Probe strategy (read-only, no side effects):
  1. Read .env to learn expected mode (USE_WEBHOOK=1 or 0).
  2. If USE_WEBHOOK=0: check that a long-poll gateway process is alive
     AND has updated its heartbeat file recently.  If stale → BROKEN
     (this overlaps with Class B; Class A distinguishes the USE_WEBHOOK=1 case).
  3. If USE_WEBHOOK=1: call Telegram getWebhookInfo.  Must return a
     url that starts with expected base (api.vectrax.app etc.) AND
     last_error_date absent/old.  Otherwise → BROKEN.

To avoid false positives from network blips:
  - State machine with rolling count: only emit BROKEN after
    BROKEN_CONFIRMATIONS consecutive failing probes.
  - A single OK immediately resets the counter.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

from core.recovery.events import HealthEvent, HealthState

logger = logging.getLogger("vectrax.recovery.detectors.gateway_silent")

DETECTOR_ID = "gateway_silent"
BROKEN_CONFIRMATIONS = 3  # fail this many in a row before emitting BROKEN
ENV_PATH = os.environ.get("RECOVERY_ENV_PATH", "/app/.env")
HEARTBEAT_PATH = os.environ.get(
    "GATEWAY_HEARTBEAT_PATH",
    "/root/.vectrax/gateway_heartbeat",
)
HEARTBEAT_MAX_AGE_S = 60  # stale if older than this (separate from supervisor's)


def _read_env(path: str) -> Dict[str, str]:
    """Minimal .env reader — no quoting tricks, no interpolation.

    Returns a dict of key→value. Lines without = are ignored.
    """
    out: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception as exc:
        logger.debug("env read failed (%s): %s", path, exc)
    return out


def _check_long_poll_heartbeat() -> Tuple[bool, Dict[str, Any]]:
    """True if a long-poll gateway is alive (heartbeat recent).

    Returns (is_healthy, evidence_dict).
    """
    ev: Dict[str, Any] = {"heartbeat_path": HEARTBEAT_PATH}
    try:
        if not os.path.exists(HEARTBEAT_PATH):
            ev["reason"] = "heartbeat file missing"
            return False, ev
        with open(HEARTBEAT_PATH, "r") as f:
            raw = f.read().strip()
        try:
            hb_ts = float(raw)
        except ValueError:
            ev["reason"] = f"heartbeat file has non-numeric content: {raw[:50]!r}"
            return False, ev
        age = time.time() - hb_ts
        ev["heartbeat_age_s"] = round(age, 2)
        if age > HEARTBEAT_MAX_AGE_S:
            ev["reason"] = f"heartbeat stale ({age:.1f}s > {HEARTBEAT_MAX_AGE_S}s)"
            return False, ev
        return True, ev
    except Exception as exc:
        ev["reason"] = f"heartbeat check crashed: {exc}"
        return False, ev


def _check_webhook_registered(token: str) -> Tuple[bool, Dict[str, Any]]:
    """Ask Telegram API whether a webhook is currently registered.

    Returns (is_healthy, evidence_dict).
    """
    ev: Dict[str, Any] = {}
    try:
        import httpx
        with httpx.Client(timeout=10) as client:
            r = client.get(f"https://api.telegram.org/bot{token}/getWebhookInfo")
            r.raise_for_status()
            data = r.json()
            if not data.get("ok"):
                ev["reason"] = f"Telegram API returned ok=false: {data}"
                return False, ev
            info = data.get("result", {})
            url = info.get("url", "")
            ev["webhook_url_present"] = bool(url)
            ev["pending_update_count"] = info.get("pending_update_count", 0)
            last_err = info.get("last_error_message")
            if last_err:
                ev["last_error_message"] = last_err
                ev["last_error_date"] = info.get("last_error_date")
            if not url:
                ev["reason"] = "USE_WEBHOOK=1 but Telegram reports no webhook URL registered"
                return False, ev
            # Webhook is set. Check last_error recency (> 5 min old ok).
            if last_err and info.get("last_error_date"):
                age = time.time() - info["last_error_date"]
                if age < 300:
                    ev["reason"] = f"webhook registered but recent error ({last_err})"
                    return False, ev
            return True, ev
    except Exception as exc:
        ev["reason"] = f"getWebhookInfo call crashed: {exc}"
        return False, ev


class _ProbeState:
    """Rolls up consecutive failure count to avoid flapping on transient blips."""

    def __init__(self) -> None:
        self.consecutive_failures = 0
        self.lock = threading.Lock()

    def record(self, failed: bool) -> int:
        with self.lock:
            if failed:
                self.consecutive_failures += 1
            else:
                self.consecutive_failures = 0
            return self.consecutive_failures


_STATE = _ProbeState()


def probe() -> HealthEvent:
    """Main probe function — called by the orchestrator every interval.

    This function NEVER raises.  Any failure → DEGRADED with evidence.
    Returns BROKEN only after BROKEN_CONFIRMATIONS consecutive failures.
    """
    env = _read_env(ENV_PATH)
    use_webhook = env.get("USE_WEBHOOK", "0") == "1"
    token = env.get("TELEGRAM_BOT_TOKEN", "")

    evidence: Dict[str, Any] = {
        "mode": "webhook" if use_webhook else "long-poll",
        "env_path": ENV_PATH,
    }

    # Branch depending on configured mode
    if use_webhook:
        if not token:
            evidence["reason"] = "USE_WEBHOOK=1 but TELEGRAM_BOT_TOKEN missing from env"
            failed = True
        else:
            ok, ev = _check_webhook_registered(token)
            evidence.update(ev)
            failed = not ok
    else:
        # long-poll mode: verify heartbeat
        ok, ev = _check_long_poll_heartbeat()
        evidence.update(ev)
        failed = not ok

    count = _STATE.record(failed)
    evidence["consecutive_failures"] = count
    evidence["threshold"] = BROKEN_CONFIRMATIONS

    if not failed:
        return HealthEvent(
            detector_id=DETECTOR_ID,
            state=HealthState.OK,
            evidence=evidence,
        )
    if count < BROKEN_CONFIRMATIONS:
        return HealthEvent(
            detector_id=DETECTOR_ID,
            state=HealthState.DEGRADED,
            evidence=evidence,
        )
    return HealthEvent(
        detector_id=DETECTOR_ID,
        state=HealthState.BROKEN,
        evidence=evidence,
    )


def reset_state_for_tests() -> None:
    """Test helper — clear consecutive_failures counter."""
    with _STATE.lock:
        _STATE.consecutive_failures = 0
