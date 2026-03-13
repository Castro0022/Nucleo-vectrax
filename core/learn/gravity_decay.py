"""
Vectrax Gravity Decay Engine
=============================
GRAVITY_DECAY_V1 parameters:

    HOT  →  WARM   after 7 days idle
    WARM →  COLD   after 45 days idle
    COLD →  DEEP   after 180 days idle
    DEEP →  (never erased)

Anti-amnesia: high-impact events decay 3× slower (decay_factor = 3.0).
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Dict, Tuple

from core.learn.schemas import GravityRecord, Tier

# GRAVITY_DECAY_V1 half-life in days  (base values, before decay_factor)
DECAY_THRESHOLDS: Dict[str, Tuple[int, str]] = {
    # current_tier: (idle_days_to_demote, target_tier)
    Tier.HOT.value:  (7,   Tier.WARM.value),
    Tier.WARM.value: (45,  Tier.COLD.value),
    Tier.COLD.value: (180, Tier.DEEP.value),
    # DEEP never demotes further
}


def _idle_days(rec: GravityRecord) -> float:
    """Days since last_seen (or now if parse fails)."""
    try:
        last = datetime.fromisoformat(rec.last_seen)
    except (ValueError, TypeError):
        return 0.0
    return max((datetime.now(timezone.utc) - last).total_seconds() / 86400, 0.0)


def should_demote(rec: GravityRecord) -> bool:
    """Return True if the record has been idle long enough to decay."""
    rule = DECAY_THRESHOLDS.get(rec.tier)
    if rule is None:
        return False
    base_days, _ = rule
    effective_days = base_days * rec.decay_factor  # anti-amnesia
    return _idle_days(rec) >= effective_days


def demote(rec: GravityRecord) -> str | None:
    """
    If the record qualifies for demotion, move it one tier cooler and
    return the new tier name.  Otherwise return None.
    """
    rule = DECAY_THRESHOLDS.get(rec.tier)
    if rule is None:
        return None
    _, target = rule
    base_days = rule[0]
    effective_days = base_days * rec.decay_factor
    if _idle_days(rec) >= effective_days:
        rec.tier = target
        return target
    return None


def apply_decay(index) -> Dict[str, str]:
    """
    Scan every record in the gravity index, demote those past their
    threshold, persist.

    Parameters
    ----------
    index : GravityIndex
        The gravity index instance (imported lazily to avoid circular).

    Returns
    -------
    dict[fingerprint, new_tier]  for records that were demoted.
    """
    records = index.load_raw()
    demoted: Dict[str, str] = {}
    for fp, rec in records.items():
        new_tier = demote(rec)
        if new_tier is not None:
            demoted[fp] = new_tier
    if demoted:
        index.update_records(records)
    return demoted
