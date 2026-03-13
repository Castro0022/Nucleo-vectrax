"""
Vectrax Resonance Engine
=========================
Law 5 — Cognitive Silence: if there is no sufficient resonance between a new
event and the gravity memory, the system stays silent.

Resonance is a composite score [0, 1] derived from:
    - tier weight     (HOT=1.0, WARM=0.7, COLD=0.4, DEEP=0.2)
    - hit frequency   (normalised)
    - CC score        (cognitive consistency)

If resonance < SILENCE_THRESHOLD the system MUST NOT propose anything.
"""

from __future__ import annotations

from typing import Optional

from core.learn.schemas import GravityRecord, Tier

SILENCE_THRESHOLD = 0.30

# Weights for the resonance components
W_TIER = 0.40
W_FREQ = 0.30
W_CC   = 0.30

# Max expected frequency for normalisation (events / day)
MAX_FREQ = 50.0


def _tier_weight(tier_value: str) -> float:
    try:
        return Tier(tier_value).weight
    except ValueError:
        return 0.0


def compute_resonance(rec: GravityRecord) -> float:
    """
    Compute resonance score ∈ [0, 1] for a single GravityRecord.

    Components:
        tier_component  = tier_weight (HOT=1, WARM=0.6, COLD=0.3, DEEP=0.1)
        freq_component  = min(rec.freq / MAX_FREQ, 1.0)
        cc_component    = rec.cc_score   (already 0-1)
    """
    tier_c = _tier_weight(rec.tier)
    freq_c = min(rec.freq / MAX_FREQ, 1.0)
    cc_c   = max(0.0, min(rec.cc_score, 1.0))

    score = W_TIER * tier_c + W_FREQ * freq_c + W_CC * cc_c
    return round(max(0.0, min(score, 1.0)), 4)


def should_speak(rec: Optional[GravityRecord]) -> bool:
    """
    Return True only when the system has enough resonance to justify
    proposing something.  If rec is None (unknown pattern), stay silent.
    """
    if rec is None:
        return False
    return compute_resonance(rec) >= SILENCE_THRESHOLD
