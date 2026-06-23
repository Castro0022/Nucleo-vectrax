"""
Domain Knowledge Library — Cross-Tenant Pattern Elevation
============================================================
Extracts mature patterns from individual tenants, strips all
identifying data, and stores them as reusable domain-level knowledge.

New tenants in the same domain start with these priors instead of
learning from zero — accelerating pattern validation from months to days.

Architecture:
  Tenant pattern (mature)
    → strip PII (tenant_id, timestamps, raw data)
    → abstract signature (event_type + conditions + stats)
    → merge into domain library (weighted average if exists)
    → new tenants get priors on creation

Privacy guarantees:
  - No tenant_id stored in the library
  - No raw data, customer names, or timestamps
  - Only statistical signatures (win_rate, expectancy, sample_size)
  - contributing_tenants is a count, not a list of IDs

Storage: ~/.vectrax/domain_library/{domain}.json
One file per domain. Portable, no DB dependency.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.domain_knowledge")

_LIBRARY_DIR = os.path.join(os.path.expanduser("~"), ".vectrax", "domain_library")

# Minimum thresholds for a pattern to be elevated to the domain library.
# Matches the existing is_usable criteria in pattern_memory.py.
MIN_SAMPLE = 15
MIN_WIN_RATE = 55.0
MIN_EXPECTANCY = 0.0


# ---------------------------------------------------------------------------
# Abstract Pattern — anonymous, transferable
# ---------------------------------------------------------------------------

@dataclass
class AbstractPattern:
    """A domain-level pattern stripped of all tenant-specific data."""
    signature: str            # e.g. "sale:conditions_A|conditions_B"
    domain: str               # e.g. "telefonia_retail"
    pattern_type: str         # e.g. "sale", "return", "buy:London"
    conditions_signature: str # sorted condition keys

    # Statistics (weighted average across contributing tenants)
    win_rate: float = 0.0
    expectancy: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    sample_size: int = 0      # total observations across all tenants
    confidence: str = "LOW"

    # Provenance (anonymous)
    contributing_tenants: int = 0  # how many DISTINCT tenants confirmed this
    first_elevated: float = 0.0
    last_confirmed: float = 0.0

    # Per-tenant latest snapshot, keyed by an irreversible salted hash of the
    # tenant_id (NOT the id itself). Enables idempotent re-elevation: the same
    # tenant updates its own contribution instead of inflating the totals.
    # Aggregates above are always recomputed from these snapshots.
    tenant_contributions: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "AbstractPattern":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @property
    def is_strong(self) -> bool:
        """A pattern confirmed by 2+ tenants with good stats."""
        return (
            self.contributing_tenants >= 2
            and self.win_rate >= MIN_WIN_RATE
            and self.expectancy > MIN_EXPECTANCY
        )


# ---------------------------------------------------------------------------
# Persistence — one JSON file per domain
# ---------------------------------------------------------------------------

def _library_path(domain: str) -> str:
    return os.path.join(_LIBRARY_DIR, f"{domain}.json")


def _load_library(domain: str) -> Dict[str, AbstractPattern]:
    path = _library_path(domain)
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            raw = json.load(f)
        return {
            k: AbstractPattern.from_dict(d) for k, d in raw.items()
        }
    except Exception as exc:
        logger.warning("load domain library %s failed: %s", domain, exc)
        return {}


def _save_library(domain: str, lib: Dict[str, AbstractPattern]) -> None:
    try:
        os.makedirs(_LIBRARY_DIR, exist_ok=True)
        with open(_library_path(domain), "w") as f:
            json.dump(
                {k: p.to_dict() for k, p in lib.items()},
                f, indent=2, ensure_ascii=False,
            )
    except Exception as exc:
        logger.error("save domain library %s failed: %s", domain, exc)


# ---------------------------------------------------------------------------
# Pattern abstraction — strip PII, keep only statistical signature
# ---------------------------------------------------------------------------

def _make_signature(pattern_type: str, conditions: str) -> str:
    """Create an anonymous signature from pattern type + conditions."""
    return f"{pattern_type}:{conditions}"


def _tenant_hash(tenant_id: str) -> str:
    """Irreversible, salted hash used only to dedupe tenant contributions.

    Never stored or exposed as a tenant_id — it cannot be reversed back to one.
    """
    return hashlib.sha256(("vx_domain_knowledge:" + (tenant_id or "")).encode()).hexdigest()[:16]


def _compute_confidence(sample_size: int, win_rate: float) -> str:
    if sample_size >= 30 and win_rate >= 65:
        return "HIGH"
    if sample_size >= 15 and win_rate >= 55:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def elevate_pattern(
    domain: str,
    pattern_type: str,
    conditions_signature: str,
    win_rate: float,
    expectancy: float,
    avg_win_pct: float,
    avg_loss_pct: float,
    sample_size: int,
    tenant_id: str,  # used only for dedup, NOT stored
) -> Optional[AbstractPattern]:
    """
    Elevate a mature tenant pattern into the domain knowledge library.

    If the same signature already exists, merge via weighted average.
    If new, create it as a fresh domain pattern.

    Returns the elevated/merged AbstractPattern, or None on failure.
    """
    # Validate maturity
    if sample_size < MIN_SAMPLE:
        return None
    if win_rate < MIN_WIN_RATE:
        return None
    if expectancy <= MIN_EXPECTANCY:
        return None

    sig = _make_signature(pattern_type, conditions_signature)
    now = time.time()
    th = _tenant_hash(tenant_id)

    lib = _load_library(domain)
    existing = lib.get(sig)
    is_new_pattern = existing is None

    if is_new_pattern:
        existing = AbstractPattern(
            signature=sig,
            domain=domain,
            pattern_type=pattern_type,
            conditions_signature=conditions_signature,
            first_elevated=now,
        )

    # Record/refresh THIS tenant's latest snapshot. Re-elevating the same
    # tenant simply overwrites its prior snapshot (idempotent) instead of
    # double-counting observations or faking new contributing tenants.
    is_new_tenant = th not in existing.tenant_contributions
    existing.tenant_contributions[th] = {
        "sample_size": int(sample_size),
        "win_rate": round(win_rate, 1),
        "expectancy": round(expectancy, 3),
        "avg_win_pct": round(avg_win_pct, 3),
        "avg_loss_pct": round(avg_loss_pct, 3),
    }

    # Recompute aggregates from per-tenant snapshots (sample-size weighted).
    contribs = list(existing.tenant_contributions.values())
    total_n = sum(c["sample_size"] for c in contribs)
    if total_n > 0:
        existing.win_rate = round(sum(c["win_rate"] * c["sample_size"] for c in contribs) / total_n, 1)
        existing.expectancy = round(sum(c["expectancy"] * c["sample_size"] for c in contribs) / total_n, 3)
        existing.avg_win_pct = round(sum(c["avg_win_pct"] * c["sample_size"] for c in contribs) / total_n, 3)
        existing.avg_loss_pct = round(sum(c["avg_loss_pct"] * c["sample_size"] for c in contribs) / total_n, 3)
    existing.sample_size = total_n
    existing.contributing_tenants = len(existing.tenant_contributions)
    existing.last_confirmed = now
    existing.confidence = _compute_confidence(existing.sample_size, existing.win_rate)

    lib[sig] = existing
    _save_library(domain, lib)

    if is_new_pattern:
        logger.info(
            "[DOMAIN] Elevated NEW %s | %s | WR=%.0f%% E=%+.3f%% | N=%d",
            domain, sig, existing.win_rate, existing.expectancy, existing.sample_size,
        )
    elif is_new_tenant:
        logger.info(
            "[DOMAIN] Reinforced %s | %s | WR=%.0f%% E=%+.3f%% | tenants=%d N=%d",
            domain, sig, existing.win_rate, existing.expectancy,
            existing.contributing_tenants, existing.sample_size,
        )
    else:
        logger.debug(
            "[DOMAIN] Updated %s | %s | tenants=%d N=%d (same tenant)",
            domain, sig, existing.contributing_tenants, existing.sample_size,
        )
    return existing


def get_domain_priors(domain: str) -> List[AbstractPattern]:
    """Return all elevated patterns for a domain, sorted by expectancy."""
    lib = _load_library(domain)
    return sorted(lib.values(), key=lambda p: p.expectancy, reverse=True)


def get_domain_summary(domain: str) -> Dict[str, Any]:
    """Summary stats for a domain's knowledge library."""
    patterns = get_domain_priors(domain)
    if not patterns:
        return {"domain": domain, "patterns": 0}
    return {
        "domain": domain,
        "patterns": len(patterns),
        "strong_patterns": sum(1 for p in patterns if p.is_strong),
        "avg_win_rate": round(sum(p.win_rate for p in patterns) / len(patterns), 1),
        "avg_expectancy": round(sum(p.expectancy for p in patterns) / len(patterns), 3),
        "total_observations": sum(p.sample_size for p in patterns),
        "max_contributing_tenants": max(p.contributing_tenants for p in patterns),
    }


def seed_tenant_priors(domain: str) -> List[Dict[str, Any]]:
    """
    Generate gravity events from domain priors for a new tenant.

    Returns a list of dicts ready to be fed into gravity_engine.record_event().
    Priors get reduced weight (impact="low") and a "domain_prior" tag
    so they can be distinguished from first-hand observations.
    """
    priors = get_domain_priors(domain)
    if not priors:
        return []

    events = []
    for p in priors:
        # Only seed patterns with meaningful confidence
        if p.sample_size < MIN_SAMPLE or p.win_rate < MIN_WIN_RATE:
            continue

        events.append({
            "fingerprint": f"prior:{p.signature}",
            "cc_score": min(p.win_rate / 100, 1.0),
            "impact": "low",  # priors start with low weight — must be confirmed locally
            "domain": domain,
            "intent": p.pattern_type,
            "outcome": f"PRIOR WR={p.win_rate:.0f}% E={p.expectancy:+.3f}% (from {p.contributing_tenants} tenants)",
            "summary": f"[DOMAIN PRIOR] {p.pattern_type} | {p.conditions_signature[:60]}",
            "source": "domain_prior",
        })

    logger.info(
        "[DOMAIN] Generated %d priors for domain %s",
        len(events), domain,
    )
    return events


def list_domains() -> List[str]:
    """List all domains that have a knowledge library."""
    if not os.path.exists(_LIBRARY_DIR):
        return []
    return [
        f.replace(".json", "")
        for f in os.listdir(_LIBRARY_DIR)
        if f.endswith(".json")
    ]


def try_elevate_from_gravity(domain: str, tenant_id: str) -> int:
    """
    Scan the gravity engine for mature patterns in a domain
    and elevate any that qualify. Returns count of elevated patterns.

    Called as a fire-and-forget hook — never raises.
    """
    elevated = 0
    try:
        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()
        records = [r for r in gi.all_records() if r.domain == domain]

        for r in records:
            # Only elevate records with sufficient observations
            if r.hits < MIN_SAMPLE:
                continue
            if r.cc_score < (MIN_WIN_RATE / 100):
                continue

            result = elevate_pattern(
                domain=domain,
                pattern_type=r.intent or "observation",
                conditions_signature=r.fingerprint,
                win_rate=round(r.cc_score * 100, 1),
                expectancy=round(r.cc_score * r.freq, 3),
                avg_win_pct=round(r.cc_score * 100, 3),
                avg_loss_pct=round((1 - r.cc_score) * 100, 3),
                sample_size=r.hits,
                tenant_id=tenant_id,
            )
            if result:
                elevated += 1
    except Exception as exc:
        logger.debug("try_elevate_from_gravity failed: %s", exc)
    return elevated
