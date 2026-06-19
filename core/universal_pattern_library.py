"""
Universal Pattern Library (UPL) — Cross-Domain Meta-Pattern Layer
===================================================================
Extracts domain-agnostic structural patterns from domain libraries
and offers them as observation hypotheses — never as operational signals.

Architecture:
  Domain libraries (freight, market, retail, etc.)
    → UPL scans for structural similarities across domains
    → Produces MetaPattern hypotheses (e.g. "demand_surge → price_spike")
    → Hypotheses are tagged HYPOTHESIS, never CONFIRMED
    → Consumers can use them to prioritize observation, never to act

Strict rules:
  - NO tenant data access. Reads only from domain_knowledge libraries.
  - NO modification of domain_knowledge, etoro connectors, or tenant modules.
  - NO cross-domain data transfer. Only structural shape comparison.
  - NO operational conclusions. Output is "observe this" not "do this".
  - MetaPatterns are stripped of all domain-specific terminology.

Storage: ~/.vectrax/universal_pattern_library.json

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.upl")

_UPL_PATH = os.path.join(os.path.expanduser("~"), ".vectrax", "universal_pattern_library.json")

# ---------------------------------------------------------------------------
# Prohibited terms — prevent domain-specific or sensitive content from
# leaking into universal patterns. Matched as WHOLE WORDS only.
# ---------------------------------------------------------------------------

_PROHIBITED_TERMS = [
    # PII / identity
    "tenant", "tenant_id", "user_id", "customer", "client", "employee",
    "name", "email", "phone", "address", "ssn", "password", "credential",
    # Domain-specific operational terms (too narrow for universal patterns)
    "ticker", "isin", "cusip", "vin", "sku", "upc", "ndc",
    # Financial specifics
    "margin_call", "leverage", "stop_loss", "take_profit",
    # Medical specifics
    "diagnosis", "prescription", "dosage", "patient",
    # Legal
    "lawsuit", "verdict", "sentence",
]

_PROHIBITED_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in _PROHIBITED_TERMS) + r")\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# MetaPattern — domain-agnostic structural hypothesis
# ---------------------------------------------------------------------------

@dataclass
class MetaPattern:
    """A universal structural pattern observed across multiple domains."""
    meta_id: str                  # e.g. "MP-demand_surge_price_response"
    structure: str                # abstract description of the pattern shape
    source_domains: List[str]     # which domains contributed (names only, no data)
    confidence: str = "HYPOTHESIS"  # always HYPOTHESIS until locally confirmed
    contributing_domain_count: int = 0
    avg_win_rate: float = 0.0
    avg_expectancy: float = 0.0
    total_observations: int = 0
    created_at: float = 0.0
    last_updated: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "MetaPattern":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_meta_pattern(mp: MetaPattern) -> tuple:
    """
    Validate a meta-pattern against safety rules.

    Returns (is_valid, reasons) where reasons lists violations.
    Uses whole-word matching to avoid false positives
    (e.g. "customer" blocked but "customization" allowed).
    """
    reasons = []

    # Check structure text for prohibited terms
    matches = _PROHIBITED_RE.findall(mp.structure)
    if matches:
        reasons.append(f"prohibited terms in structure: {matches}")

    # Check meta_id for prohibited terms
    id_matches = _PROHIBITED_RE.findall(mp.meta_id.replace("_", " ").replace("-", " "))
    if id_matches:
        reasons.append(f"prohibited terms in meta_id: {id_matches}")

    # Must have at least 2 source domains to be universal
    if mp.contributing_domain_count < 2:
        reasons.append(f"need 2+ domains, have {mp.contributing_domain_count}")

    # Confidence must be HYPOTHESIS
    if mp.confidence != "HYPOTHESIS":
        reasons.append(f"confidence must be HYPOTHESIS, got {mp.confidence}")

    # Must have observations
    if mp.total_observations < 1:
        reasons.append("no observations")

    return (len(reasons) == 0, reasons)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load_upl() -> Dict[str, MetaPattern]:
    if not os.path.isfile(_UPL_PATH):
        return {}
    try:
        with open(_UPL_PATH) as f:
            raw = json.load(f)
        return {k: MetaPattern.from_dict(v) for k, v in raw.items()}
    except Exception as exc:
        logger.warning("UPL load failed: %s", exc)
        return {}


def _save_upl(lib: Dict[str, MetaPattern]) -> None:
    try:
        os.makedirs(os.path.dirname(_UPL_PATH), exist_ok=True)
        with open(_UPL_PATH, "w") as f:
            json.dump(
                {k: p.to_dict() for k, p in lib.items()},
                f, indent=2, ensure_ascii=False,
            )
    except Exception as exc:
        logger.error("UPL save failed: %s", exc)


# ---------------------------------------------------------------------------
# Core: scan domain libraries for cross-domain structural patterns
# ---------------------------------------------------------------------------

def _abstract_pattern_type(pattern_type: str) -> str:
    """
    Convert a domain-specific pattern type to an abstract structural label.
    e.g. "delay_reported" → "degradation_event"
         "rate_change" → "value_shift"
         "capacity_update" → "resource_state_change"
    """
    mappings = {
        # Logistics/freight
        "delay_reported": "degradation_event",
        "rate_change": "value_shift",
        "capacity_update": "resource_state_change",
        "quote_request": "demand_signal",
        "load_booking": "commitment_event",
        "delivery_complete": "fulfillment_event",
        "empty_miles": "waste_signal",
        "weather_event": "external_disruption",
        # Market/trading
        "buy": "entry_long",
        "sell": "entry_short",
        "observation": "observation",
        # Retail
        "sale": "transaction_event",
        "return": "reversal_event",
        "restock": "supply_event",
        # Restaurant
        "order": "demand_signal",
        "reservation": "commitment_event",
        "complaint": "degradation_event",
        # Healthcare
        "admission": "intake_event",
        "discharge": "completion_event",
        "lab_result": "measurement_event",
        # Finance
        "transaction": "transaction_event",
        "approval": "authorization_event",
        "rejection": "denial_event",
    }
    return mappings.get(pattern_type.lower(), pattern_type.lower())


def scan_domain_libraries() -> List[MetaPattern]:
    """
    Scan all domain libraries for structural patterns that appear
    in 2+ domains. Returns new MetaPattern hypotheses.

    READS ONLY — does not modify domain libraries.
    DOES NOT access tenant data.
    """
    try:
        from core.domain_knowledge import list_domains, get_domain_priors
    except ImportError:
        logger.debug("domain_knowledge not available")
        return []

    domains = list_domains()
    if len(domains) < 2:
        return []  # need at least 2 domains for cross-domain patterns

    # Collect abstract pattern types per domain
    abstract_by_domain: Dict[str, Dict[str, Dict]] = {}  # abstract_type → {domain → stats}
    for domain in domains:
        priors = get_domain_priors(domain)
        for p in priors:
            abstract = _abstract_pattern_type(p.pattern_type)
            if abstract not in abstract_by_domain:
                abstract_by_domain[abstract] = {}
            abstract_by_domain[abstract][domain] = {
                "win_rate": p.win_rate,
                "expectancy": p.expectancy,
                "sample_size": p.sample_size,
            }

    # Find patterns appearing in 2+ domains
    now = time.time()
    meta_patterns = []
    for abstract_type, domain_stats in abstract_by_domain.items():
        if len(domain_stats) < 2:
            continue

        domains_list = list(domain_stats.keys())
        total_obs = sum(s["sample_size"] for s in domain_stats.values())
        avg_wr = sum(s["win_rate"] for s in domain_stats.values()) / len(domain_stats)
        avg_exp = sum(s["expectancy"] for s in domain_stats.values()) / len(domain_stats)

        mp = MetaPattern(
            meta_id=f"MP-{abstract_type}",
            structure=f"Cross-domain structural pattern: {abstract_type} observed in {len(domains_list)} domains",
            source_domains=domains_list,
            confidence="HYPOTHESIS",
            contributing_domain_count=len(domains_list),
            avg_win_rate=round(avg_wr, 1),
            avg_expectancy=round(avg_exp, 3),
            total_observations=total_obs,
            created_at=now,
            last_updated=now,
        )

        is_valid, reasons = validate_meta_pattern(mp)
        if is_valid:
            meta_patterns.append(mp)
        else:
            logger.debug("UPL rejected %s: %s", mp.meta_id, reasons)

    return meta_patterns


def refresh_upl() -> Dict[str, Any]:
    """
    Scan domain libraries and update the UPL.
    Returns summary of what was found/updated.
    """
    meta_patterns = scan_domain_libraries()
    lib = _load_upl()
    new_count = 0
    updated_count = 0

    for mp in meta_patterns:
        existing = lib.get(mp.meta_id)
        if existing:
            # Update stats, keep creation time
            existing.avg_win_rate = mp.avg_win_rate
            existing.avg_expectancy = mp.avg_expectancy
            existing.total_observations = mp.total_observations
            existing.contributing_domain_count = mp.contributing_domain_count
            existing.source_domains = mp.source_domains
            existing.last_updated = mp.last_updated
            lib[mp.meta_id] = existing
            updated_count += 1
        else:
            lib[mp.meta_id] = mp
            new_count += 1

    _save_upl(lib)
    logger.info(
        "[UPL] Refreshed: %d total, %d new, %d updated",
        len(lib), new_count, updated_count,
    )
    return {
        "total": len(lib),
        "new": new_count,
        "updated": updated_count,
        "patterns": [mp.to_dict() for mp in lib.values()],
    }


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

def get_all_meta_patterns() -> List[MetaPattern]:
    """Return all meta-patterns in the UPL."""
    return list(_load_upl().values())


def get_usable_hypotheses(min_domains: int = 2, min_observations: int = 10) -> List[MetaPattern]:
    """
    Return meta-patterns suitable for use as observation hypotheses.

    These are NOT operational signals — they indicate WHERE to look,
    not WHAT to do. A consumer (e.g. proposal engine, gravity scoring)
    should use these to prioritize observation, never to bypass
    local pattern confirmation.

    Args:
        min_domains: minimum number of domains that confirm the pattern
        min_observations: minimum total observations across all domains
    """
    lib = _load_upl()
    return [
        mp for mp in lib.values()
        if mp.contributing_domain_count >= min_domains
        and mp.total_observations >= min_observations
        and mp.confidence == "HYPOTHESIS"
    ]


def get_upl_summary() -> Dict[str, Any]:
    """Summary stats for the UPL."""
    lib = _load_upl()
    patterns = list(lib.values())
    if not patterns:
        return {"total": 0, "domains_covered": 0}
    all_domains = set()
    for mp in patterns:
        all_domains.update(mp.source_domains)
    return {
        "total": len(patterns),
        "domains_covered": len(all_domains),
        "avg_domain_count": round(
            sum(mp.contributing_domain_count for mp in patterns) / len(patterns), 1
        ),
        "total_observations": sum(mp.total_observations for mp in patterns),
    }
