"""
Gravity Kernel — Config Loader
================================
Carga y cachea ``config/gravity_kernel.yaml``. Si el archivo falta o está
corrupto, devuelve defaults seguros para que el kernel (en shadow mode) nunca
rompa el pipeline. Toda tabla/peso/umbral vive en el YAML, no en el código.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("vectrax.gravity_kernel.loader")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _PROJECT_ROOT / "config" / "gravity_kernel.yaml"

# Defaults seguros si el YAML no existe. Mantienen al kernel operativo en
# modo neutro sin inventar comportamiento.
_DEFAULTS: Dict[str, Any] = {
    "domain_risk_table": {
        "conversation_reply": {
            "cost_of_error": 0.20,
            "domain_risk_level": 0.20,
            "reversibility": 0.90,
        },
    },
    "source_to_action": {},
    "default_action": "conversation_reply",
    "correction_markers": [],
    "frustration_markers": [],
    "clarity_markers": [],
    "rhythm_weights": {
        "base": 1.0,
        "correction_unit": 0.15, "correction_cap": 0.45,
        "frustration_unit": 0.20, "frustration_cap": 0.50,
        "topic_switch_unit": 0.10, "topic_switch_cap": 0.30,
        "misread_unit": 0.20, "misread_cap": 0.60,
        "same_axis_unit": 0.08, "same_axis_cap": 0.24,
        "unanswered_confusion_penalty": 0.25,
    },
    "rhythm_bands": {
        "advance": 0.75,
        "respond_short_and_anchor": 0.50,
        "pause_and_calibrate": 0.30,
        "stop_generation_and_ask_axis": 0.0,
    },
    "cause_effect_weights": {
        "base": 0.50,
        "win_rate_coeff": 0.40,
        "expectancy_coeff": 0.30,
        "confidence_coeff": 0.25,
        "cost_of_error_coeff": 0.35,
        "domain_risk_coeff": 0.20,
        "reversibility_coeff": 0.15,
        "correction_history_unit": 0.05,
        "correction_history_cap": 0.25,
    },
    "cause_effect_bands": {
        "execute": 0.80,
        "execute_with_audit": 0.65,
        "simulate_or_ask_confirmation": 0.45,
        "hold_and_collect_more_evidence": 0.25,
        "block": 0.0,
    },
    "neutral_defaults": {"win_rate": 0.50, "expectancy": 0.0, "confidence": 0.50},
    "future_decision_hierarchy": [],
}

_cache: Optional[Dict[str, Any]] = None


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow-merge ``over`` onto ``base`` (one level deep for dict values)."""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            merged = dict(out[k])
            merged.update(v)
            out[k] = merged
        else:
            out[k] = v
    return out


def get_config(force_reload: bool = False) -> Dict[str, Any]:
    """Return the cached kernel config, loading the YAML over the defaults."""
    global _cache
    if _cache is not None and not force_reload:
        return _cache

    loaded: Dict[str, Any] = {}
    try:
        import yaml
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
        else:
            logger.debug("gravity_kernel.yaml not found at %s; using defaults", _CONFIG_PATH)
    except Exception as exc:  # corrupt YAML, missing pyyaml, etc.
        logger.debug("gravity_kernel config load failed (using defaults): %s", exc)
        loaded = {}

    _cache = _merge(_DEFAULTS, loaded)
    return _cache
