"""
Ledger for the recovery engine.

Append-only JSONL at vault/recovery_ledger.jsonl by default. Each line is
a timestamped JSON object describing either a HealthEvent or an
ExecutionResult. Never modified, never truncated — rotated only when it
exceeds a size threshold (~10 MB).

Intentionally simple. Does NOT integrate with core/operator/ledger_bridge
yet — that bridge ties into the cognition/ledger which has richer schema.
If this proves useful we can migrate in v2.

Thread-safe via a module-level lock (writes are rare, contention minimal).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger("vectrax.recovery.ledger")

# Ledger path resolution order (first writable wins):
#   1. $RECOVERY_LEDGER_PATH (explicit override, mostly for tests)
#   2. /var/log/vectrax/recovery_ledger.jsonl  (preferred: operational audit,
#      separate from vault/ which holds cognitive memory with different
#      semantics — backup, rotation, privacy, export).
#   3. /app/recovery/ledger.jsonl              (fallback inside container)
#   4. /tmp/vectrax_recovery_ledger.jsonl      (last resort, tests, volatile)
#
# NEVER /app/vault/ — that is cognitive memory land, not operational audit.
_PREFERRED_PATHS = [
    "/var/log/vectrax/recovery_ledger.jsonl",
    "/app/recovery/ledger.jsonl",
    "/tmp/vectrax_recovery_ledger.jsonl",
]


def _resolve_default_path() -> str:
    """Pick the first path whose parent directory is creatable+writable.

    Called once at import-time; result is cached in _DEFAULT_LEDGER_PATH.
    """
    override = os.environ.get("RECOVERY_LEDGER_PATH")
    if override:
        return override
    for candidate in _PREFERRED_PATHS:
        parent = os.path.dirname(candidate)
        try:
            os.makedirs(parent, exist_ok=True)
            # Test writability with a probe file
            probe = os.path.join(parent, ".vectrax_ledger_probe")
            with open(probe, "w") as f:
                f.write("x")
            os.remove(probe)
            return candidate
        except Exception:
            continue
    # All candidates failed; use /tmp as a last resort even if we couldn't probe it
    return "/tmp/vectrax_recovery_ledger.jsonl"


_DEFAULT_LEDGER_PATH = _resolve_default_path()
_ROTATE_THRESHOLD_BYTES = 10 * 1024 * 1024  # 10 MB

_write_lock = threading.Lock()


def _ensure_parent(path: str) -> None:
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)


def _rotate_if_needed(path: str) -> None:
    """Rotate the ledger if it grows past _ROTATE_THRESHOLD_BYTES.

    Keeps one archive at path + ".1"; the previous archive is overwritten.
    This is intentionally minimal — the ledger is for short-term audit,
    not long-term analytics.
    """
    try:
        if os.path.exists(path) and os.path.getsize(path) >= _ROTATE_THRESHOLD_BYTES:
            archive = path + ".1"
            if os.path.exists(archive):
                os.remove(archive)
            os.rename(path, archive)
            logger.info("Recovery ledger rotated to %s", archive)
    except Exception as exc:
        logger.warning("Ledger rotation failed (non-fatal): %s", exc)


def write_entry(
    kind: str,
    payload: Dict[str, Any],
    path: Optional[str] = None,
) -> Optional[str]:
    """Append an entry to the ledger.

    Args:
      kind:    "event" | "execution" | "escalation" | "startup" | "shutdown"
      payload: JSON-serializable dict.  Do NOT include secrets; the
               caller is responsible for redaction.
      path:    override the default ledger path (mostly for tests).

    Returns:
      The entry's ledger_entry_id (UUID) on success, None on failure.
      Failure is NEVER raised — ledger writes must not break the caller.
    """
    target = path or _DEFAULT_LEDGER_PATH
    entry_id = uuid.uuid4().hex[:16]
    entry = {
        "ledger_entry_id": entry_id,
        "kind": kind,
        "ts": time.time(),
        "ts_human": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "payload": payload,
    }
    try:
        _ensure_parent(target)
        with _write_lock:
            _rotate_if_needed(target)
            with open(target, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return entry_id
    except Exception as exc:
        logger.warning("Ledger write failed (non-fatal): %s", exc)
        return None
