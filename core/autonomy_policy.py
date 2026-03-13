"""
Vectrax Progressive Autonomy Policy
=====================================
Defines the three-zone classification system and auto-apply rules
for supervised autonomy.

Zones:
  SACRED_CORE  — Never auto-apply. Requires human confirmation always.
  SEMI_SAFE    — Always requires human confirmation.
  FLEXIBLE     — Auto-apply possible when global switch is ON and
                 all thresholds are met.

Hard Limits:
  Certain paths are ALWAYS blocked from automatic changes regardless
  of zone or switch state. These include secrets, credentials, .env,
  vault, and database files.

Usage::

    from core.autonomy_policy import classify_path, can_auto_apply, Zone

    zone = classify_path("core/governor.py")
    # => Zone.SACRED_CORE

    allowed, reason = can_auto_apply(
        path="docs/README.md",
        risk_score=0.10,
        confidence_score=0.95,
        diff_lines=12,
    )
"""

from __future__ import annotations

import json
import os
import re
from enum import Enum
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Zone definition
# ---------------------------------------------------------------------------

class Zone(str, Enum):
    SACRED_CORE = "SACRED_CORE"
    SEMI_SAFE   = "SEMI_SAFE"
    FLEXIBLE    = "FLEXIBLE"


ZONE_LABELS: Dict[Zone, str] = {
    Zone.SACRED_CORE: "Core Sagrado",
    Zone.SEMI_SAFE:   "Zona Semi-Segura",
    Zone.FLEXIBLE:    "Zona Flexible",
}


# ---------------------------------------------------------------------------
# Zone mapping — real paths from the Vectrax repo
# ---------------------------------------------------------------------------

# Paths that match the START of a relative path (directories end with /)
SACRED_CORE_PATHS: List[str] = [
    "core/governor.py",
    "core/risk_engine.py",
    "core/state_manager.py",
    "core/smoke.py",
    "core/autonomy_policy.py",
    "core/policy_router.py",
    "core/causal_ledger.py",
    "core/resilience/",
    "core/routing/",
    "core/abstraction/",
    "schema.sql",
    ".env",
    "vault/",
    "config/config.yaml",
    "config/autonomy.json",
]

SEMI_SAFE_PATHS: List[str] = [
    "core/proposal_engine.py",
    "core/autopatch.py",
    "core/meta_loop.py",
    "core/shadow_mode.py",
    "core/action_sandbox.py",
    "core/action_executor.py",
    "core/ingest.py",
    "core/memory_sqlite.py",
    "core/providers/",
    "core/workflows/",
    "core/__init__.py",
    "cli/",
    "scripts/",
    "setup.py",
    "app.py",
    "nucleo.py",
    "vectrax_engine.py",
    "vectrax_unified.py",
    "vectrax_ptt.py",
    "vectrax_presencia.py",
    "vectrax_presencia_ptt.py",
    "vectrax_habla_unico.py",
    "vectrax_start.sh",
    "activate_vx.sh",
    "requirements.txt",
    "requirements_new.txt",
]

FLEXIBLE_PATHS: List[str] = [
    "core/daily_report.py",
    "core/reporter.py",
    "core/observability/",
    "core/rules.py",
    "core/alerta.py",
    "core/alerta_stop.py",
    "docs/",
    "reports/",
    "logs/",
    "test_",                    # matches test_*.py at root
    "demo_",                    # matches demo_*.py
    "current_focus.txt",
    "inbox_done/",
]


# ---------------------------------------------------------------------------
# Hard limits — NEVER auto-apply, regardless of zone or switch
# ---------------------------------------------------------------------------

HARD_LIMIT_PATTERNS: List[str] = [
    ".env",
    "vault/",
    ".git/",
    "keys/",
    "secrets/",
]

HARD_LIMIT_EXTENSIONS: List[str] = [
    ".db",
    ".db-shm",
    ".db-wal",
    ".pem",
    ".key",
]

HARD_LIMIT_NAME_KEYWORDS: List[str] = [
    "secret",
    "credential",
    "token",
    "api_key",
    "apikey",
    "password",
    "private_key",
]


# ---------------------------------------------------------------------------
# Default thresholds
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLDS = {
    "auto_apply_enabled": False,
    "max_risk_score": 0.15,
    "min_confidence_score": 0.90,
    "max_diff_lines": 50,
}

# Config path
BASE_DIR = os.path.expanduser("~/Vectrax")
CONFIG_PATH = os.path.join(BASE_DIR, "config", "autonomy.json")


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_autonomy_config() -> Dict:
    """Load progressive_autonomy section from config/autonomy.json."""
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
        return cfg.get("progressive_autonomy", dict(DEFAULT_THRESHOLDS))
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_THRESHOLDS)


# ---------------------------------------------------------------------------
# Hard limit check
# ---------------------------------------------------------------------------

def is_hard_limited(path: str) -> bool:
    """
    Return True if *path* matches a hard limit rule.
    Hard-limited files can NEVER be auto-applied, regardless of zone.
    """
    rel = _normalise(path)
    name_lower = os.path.basename(rel).lower()

    # Exact path / prefix match
    for pattern in HARD_LIMIT_PATTERNS:
        if pattern.endswith("/"):
            if rel.startswith(pattern) or rel.startswith(pattern.rstrip("/")):
                return True
        elif rel == pattern or rel.startswith(pattern + "/"):
            return True

    # Extension match
    for ext in HARD_LIMIT_EXTENSIONS:
        if rel.endswith(ext):
            return True

    # Name keyword match
    for kw in HARD_LIMIT_NAME_KEYWORDS:
        if kw in name_lower:
            return True

    return False


# ---------------------------------------------------------------------------
# Zone classification
# ---------------------------------------------------------------------------

def _normalise(path: str) -> str:
    """Normalise a path to be relative to project root."""
    rel = path.replace("\\", "/")
    # Strip absolute prefix if present
    home_prefix = os.path.expanduser("~/Vectrax/")
    if rel.startswith(home_prefix):
        rel = rel[len(home_prefix):]
    elif rel.startswith("/"):
        # Try to strip BASE_DIR
        if rel.startswith(BASE_DIR + "/"):
            rel = rel[len(BASE_DIR) + 1:]
    # Strip leading ./
    if rel.startswith("./"):
        rel = rel[2:]
    return rel


def _matches_zone(rel: str, patterns: List[str]) -> bool:
    """Check if a relative path matches any zone pattern."""
    for pattern in patterns:
        if pattern.endswith("/"):
            if rel.startswith(pattern) or rel.startswith(pattern.rstrip("/")):
                return True
        else:
            if rel == pattern or rel.startswith(pattern):
                return True
    return False


def classify_path(path: str) -> Zone:
    """
    Classify a file path into one of the three zones.

    Args:
        path: absolute or relative path to the file.

    Returns:
        Zone enum value.
    """
    rel = _normalise(path)

    # Hard-limited paths are always SACRED_CORE
    if is_hard_limited(rel):
        return Zone.SACRED_CORE

    if _matches_zone(rel, SACRED_CORE_PATHS):
        return Zone.SACRED_CORE

    if _matches_zone(rel, SEMI_SAFE_PATHS):
        return Zone.SEMI_SAFE

    if _matches_zone(rel, FLEXIBLE_PATHS):
        return Zone.FLEXIBLE

    # Default: unknown paths are SEMI_SAFE (conservative)
    return Zone.SEMI_SAFE


# ---------------------------------------------------------------------------
# Auto-apply decision
# ---------------------------------------------------------------------------

def can_auto_apply(
    path: str,
    risk_score: float,
    confidence_score: float,
    diff_lines: int,
    config: Optional[Dict] = None,
) -> Tuple[bool, str]:
    """
    Determine whether a change can be auto-applied without human confirmation.

    Returns:
        (allowed: bool, reason: str)
    """
    cfg = config or load_autonomy_config()
    rel = _normalise(path)

    # --- Hard limits (absolute block) ---
    if is_hard_limited(rel):
        return False, f"HARD LIMIT: '{rel}' es un archivo protegido (secreto/credencial/config crítica)"

    # --- Global switch ---
    if not cfg.get("auto_apply_enabled", False):
        return False, "Auto-apply desactivado globalmente (auto_apply_enabled = false)"

    # --- Zone check ---
    zone = classify_path(rel)

    if zone == Zone.SACRED_CORE:
        return False, f"BLOQUEADO: '{rel}' pertenece a Core Sagrado — nunca auto-apply"

    if zone == Zone.SEMI_SAFE:
        return False, f"CONFIRMACIÓN REQUERIDA: '{rel}' pertenece a Zona Semi-Segura"

    # --- Zone is FLEXIBLE — check thresholds ---
    max_risk = cfg.get("max_risk_score", DEFAULT_THRESHOLDS["max_risk_score"])
    min_conf = cfg.get("min_confidence_score", DEFAULT_THRESHOLDS["min_confidence_score"])
    max_diff = cfg.get("max_diff_lines", DEFAULT_THRESHOLDS["max_diff_lines"])

    if risk_score >= max_risk:
        return False, (
            f"risk_score={risk_score:.3f} >= umbral {max_risk} — confirmación requerida"
        )

    if confidence_score < min_conf:
        return False, (
            f"confidence_score={confidence_score:.3f} < umbral {min_conf} — confirmación requerida"
        )

    if diff_lines > max_diff:
        return False, (
            f"diff_lines={diff_lines} > umbral {max_diff} — cambio demasiado grande"
        )

    return True, (
        f"Auto-apply permitido: zona={ZONE_LABELS[zone]}, "
        f"risk={risk_score:.3f}, confidence={confidence_score:.3f}, "
        f"diff={diff_lines} líneas"
    )


# ---------------------------------------------------------------------------
# Batch classification for proposals
# ---------------------------------------------------------------------------

def classify_proposal(
    file_paths: List[str],
    risk_score: float,
    confidence_score: float,
    diff_lines: int,
    config: Optional[Dict] = None,
) -> Dict:
    """
    Classify an entire proposal (multiple files).

    Returns dict with:
        zones: {path: zone} mapping
        highest_zone: the most restrictive zone in the proposal
        auto_apply_allowed: bool
        reason: str
        per_file: [{path, zone, auto_apply, reason}]
    """
    cfg = config or load_autonomy_config()
    per_file = []
    zones = {}
    highest = Zone.FLEXIBLE

    zone_priority = {Zone.SACRED_CORE: 3, Zone.SEMI_SAFE: 2, Zone.FLEXIBLE: 1}

    for fp in file_paths:
        zone = classify_path(fp)
        zones[fp] = zone
        allowed, reason = can_auto_apply(fp, risk_score, confidence_score, diff_lines, cfg)
        per_file.append({
            "path": fp,
            "zone": zone.value,
            "zone_label": ZONE_LABELS[zone],
            "auto_apply": allowed,
            "reason": reason,
        })
        if zone_priority.get(zone, 0) > zone_priority.get(highest, 0):
            highest = zone

    # Overall decision: ALL files must be auto-apply-able
    all_allowed = all(pf["auto_apply"] for pf in per_file)
    if not per_file:
        all_allowed = False

    if all_allowed:
        overall_reason = "Todos los archivos en Zona Flexible y dentro de umbrales"
    else:
        blocked = [pf for pf in per_file if not pf["auto_apply"]]
        overall_reason = f"{len(blocked)} archivo(s) requieren confirmación: " + \
            "; ".join(f"{b['path']} ({b['reason']})" for b in blocked[:3])

    return {
        "zones": {k: v.value for k, v in zones.items()},
        "highest_zone": highest.value,
        "highest_zone_label": ZONE_LABELS[highest],
        "auto_apply_allowed": all_allowed,
        "reason": overall_reason,
        "per_file": per_file,
    }
