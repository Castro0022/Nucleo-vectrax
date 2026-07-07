"""
Vectrax eToro — Signal Filters v2 (regime-based)  [EXPERIMENTAL / OBSERVATIONAL]
================================================================================
Evidence-based candidate-filter configuration derived from the 2026-07-07
signal-quality audit. It is measured OUT-OF-SAMPLE via PAPER-shadow and is NOT
wired into the real gate or the auto-executor.

Audit findings that motivate this config (on 271 decisive signals):
  - `DIRECCION_TENDENCIA` is the ONLY condition with predictive edge
    (present WR 44.3% vs absent 23.5%).
  - `PRECIO_EN_ZONA` (−6.2 pp WR) and `VOLUMEN_RELATIVO` (−3.6 pp) SUBTRACT edge.
  - Requiring 4/4 conditions is the worst combo (meanRet −0.066).
  - Edge is directional-by-regime:
        equities → SHORT (trend+sell equity: WR 54.9%, E +0.084, n=91)
        crypto   → LONG  (trend+buy  crypto: WR 62.5%, E +0.289, n=24)
    Likely a period regime — expressed as "trade only WITH the instrument's
    prevailing trend", encoded in an adjustable REGIME_DIRECTION map.

Nothing here changes real trading behavior. The variants are scored forward via
`variant_report()` and surfaced in the PAPER-shadow dashboard block.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

# Symbols whose ticker starts with one of these are treated as crypto.
CRYPTO_PREFIXES = ("BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "LTC", "BNB")

# Declared regime map (ADJUSTABLE hypothesis under test in shadow). Direction the
# instrument's prevailing trend favored in-sample; V1/V2 only trade WITH it.
REGIME_DIRECTION = {"crypto": "buy", "equity": "sell"}

_EDGE_CONDITION = "DIRECCION_TENDENCIA"
_HARMFUL = ("PRECIO_EN_ZONA", "VOLUMEN_RELATIVO")


def asset_class(symbol: str) -> str:
    s = (symbol or "").upper()
    return "crypto" if s.startswith(CRYPTO_PREFIXES) else "equity"


def regime_direction(symbol: str) -> str:
    return REGIME_DIRECTION.get(asset_class(symbol), "sell")


def _has(sig, cond: str) -> bool:
    return cond in (getattr(sig, "conditions_names", None) or [])


# ── Variants (signal-level predicates) ────────────────────────────────

def variant_v1(sig) -> bool:
    """V1 follow-trend (robust): trend required + direction aligned with regime."""
    return _has(sig, _EDGE_CONDITION) and \
        getattr(sig, "direction", "") == regime_direction(getattr(sig, "symbol", ""))


def variant_v2(sig) -> bool:
    """V2 aggressive: V1 + drop PRECIO_EN_ZONA + only 3/4 setups (avoid worst combo)."""
    return (
        variant_v1(sig)
        and not _has(sig, "PRECIO_EN_ZONA")
        and int(getattr(sig, "conditions_met", 0) or 0) == 3
    )


VARIANTS = {"V1_follow_trend": variant_v1, "V2_aggressive": variant_v2}


def accepting_variants(sig) -> List[str]:
    """Return the variant names that would have taken this signal."""
    return [name for name, fn in VARIANTS.items() if fn(sig)]


# ── Forward scoring (out-of-sample capable) ───────────────────────────

def _wilson_lb(k: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(max(0.0, c - m) * 100, 1)


def _stats(sigs) -> Dict[str, Any]:
    dec = [s for s in sigs if getattr(s, "status", "") in ("win", "loss")]
    n = len(dec)
    w = sum(1 for s in dec if s.status == "win")
    rets = [float(s.return_pct) for s in dec if getattr(s, "return_pct", None) is not None]
    return {
        "decisive": n,
        "win_rate": round(w / n * 100, 1) if n else 0.0,
        "wilson_lb": _wilson_lb(w, n),
        "expectancy": round(sum(rets) / len(rets), 4) if rets else 0.0,
    }


def variant_report(signals, since_ts=None) -> Dict[str, Any]:
    """Per-variant performance over resolved signals.

    Read-only aggregation; measures each variant on the SAME real signals so we
    can compare against the base rate. When ``since_ts`` is given, only signals
    CREATED at/after that timestamp are counted (out-of-sample / forward window
    used to validate the config before any promotion to real execution).
    """
    resolved = [s for s in signals if getattr(s, "status", "") in ("win", "loss", "neutral", "expired")]
    if since_ts is not None:
        resolved = [s for s in resolved if float(getattr(s, "timestamp", 0) or 0) >= float(since_ts)]
    out: Dict[str, Any] = {"base_all": _stats(resolved)}
    for name, fn in VARIANTS.items():
        out[name] = _stats([s for s in resolved if fn(s)])
    return out
