"""
core/scale/pipeline_policy.py — Declarative configuration for pipeline
profiles and tier policies.

Defines:
  - PipelineProfile: the 4 levels of complexity a request may need.
  - Tier: the 4 user tiers with different budget caps.
  - PolicyConfig: the concrete policy applied to a (tier, profile) pair.
  - get_policy_for(tier, profile) → PolicyConfig.

This file is data-only — no logic. The ScaleGovernor consumes it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Tuple


# ===========================================================================
# Pipeline profiles (request complexity)
# ===========================================================================

class PipelineProfile(str, Enum):
    """How heavy a request actually is."""

    MINIMAL = "MINIMAL"                # greetings, smalltalk
    STANDARD = "STANDARD"              # normal queries, memory lookups
    ADVANCED = "ADVANCED"              # multimodal, analysis, planning
    MISSION_CRITICAL = "MISSION_CRITICAL"  # legal/medical/financial/irreversible


# ===========================================================================
# User tiers
# ===========================================================================

class Tier(str, Enum):
    """User class with budget caps."""

    FREE = "FREE"
    PRO = "PRO"
    BUSINESS = "BUSINESS"
    INTERNAL = "INTERNAL"


# Hard cap: max engines that any pipeline can activate per tier.
# INTERNAL has a soft cap (very high) so it still appears in tracking
# but isn't practically constrained.
MAX_ENGINES_BY_TIER: Dict[Tier, int] = {
    Tier.FREE: 5,
    Tier.PRO: 8,
    Tier.BUSINESS: 12,
    Tier.INTERNAL: 1000,  # "no practical limit, but tracked"
}


# ===========================================================================
# PolicyConfig — concrete policy for a (tier, profile) pair
# ===========================================================================

@dataclass(frozen=True)
class PolicyConfig:
    """The capabilities allowed for one (tier, profile) combination.

    Used by ScaleGovernor.select_minimal_pipeline() to gate which
    engines may run for a given request.
    """

    max_engines: int                   # budget cap (engines may activate)
    allow_deep_memory: bool            # gravitational memory deep search
    allow_reasoning: bool              # multi-step LLM reasoning
    allow_multimodal: bool             # vision / audio / etc.
    allow_scenarios: bool              # what-if simulation engines
    allow_learning: bool               # write-back learning loops
    llm_model: str                     # provider hint: cheap | std | premium
    timeout_ms: int                    # hard wall-clock cap for the whole pipeline
    priority: int                      # queue priority (higher = sooner)


# Cheap/standard/premium aliases (resolved by router/load shedder later)
_MODEL_CHEAP = "cheap"          # gpt-4o-mini, etc.
_MODEL_STD = "std"              # gpt-4o, sonnet
_MODEL_PREMIUM = "premium"      # gpt-4o, opus, premium reasoning


# Base profile defaults (before tier modifies them)
_BASE_BY_PROFILE: Dict[PipelineProfile, PolicyConfig] = {
    PipelineProfile.MINIMAL: PolicyConfig(
        max_engines=2,
        allow_deep_memory=False,
        allow_reasoning=False,
        allow_multimodal=False,
        allow_scenarios=False,
        allow_learning=False,
        llm_model=_MODEL_CHEAP,
        timeout_ms=2000,
        priority=10,
    ),
    PipelineProfile.STANDARD: PolicyConfig(
        max_engines=5,
        allow_deep_memory=True,
        allow_reasoning=False,
        allow_multimodal=False,
        allow_scenarios=False,
        allow_learning=True,
        llm_model=_MODEL_STD,
        timeout_ms=8000,
        priority=5,
    ),
    PipelineProfile.ADVANCED: PolicyConfig(
        max_engines=10,
        allow_deep_memory=True,
        allow_reasoning=True,
        allow_multimodal=True,
        allow_scenarios=True,
        allow_learning=True,
        llm_model=_MODEL_STD,
        timeout_ms=15000,
        priority=3,
    ),
    PipelineProfile.MISSION_CRITICAL: PolicyConfig(
        max_engines=20,
        allow_deep_memory=True,
        allow_reasoning=True,
        allow_multimodal=True,
        allow_scenarios=True,
        allow_learning=True,
        llm_model=_MODEL_PREMIUM,
        timeout_ms=30000,
        priority=1,
    ),
}


# Profiles that FREE may NOT access in full form.
# select_minimal_pipeline() should downgrade to STANDARD-level capabilities
# (or refuse with upgrade hint) when a FREE user hits these.
_FREE_RESTRICTED_PROFILES = {
    PipelineProfile.MISSION_CRITICAL,
}


def get_policy_for(
    tier: Tier,
    profile: PipelineProfile,
) -> PolicyConfig:
    """Return the PolicyConfig for a concrete (tier, profile) pair.

    Application of tier modifiers:
      - max_engines is the MIN of the profile cap and the tier cap.
      - FREE in MISSION_CRITICAL is downgraded to ADVANCED-without-learning
        (the request still gets attention, but premium model and full
        budget are not granted; the caller can then offer an upgrade).
      - INTERNAL gets the profile's natural ceiling (no further capping).
    """
    base = _BASE_BY_PROFILE[profile]
    tier_cap = MAX_ENGINES_BY_TIER.get(tier, 5)

    # FREE in MISSION_CRITICAL → degrade
    if tier == Tier.FREE and profile in _FREE_RESTRICTED_PROFILES:
        # Use ADVANCED defaults but force a cap of 5 engines and the cheap
        # model. The caller is expected to also surface an "upgrade" hint.
        adv = _BASE_BY_PROFILE[PipelineProfile.ADVANCED]
        return PolicyConfig(
            max_engines=min(5, adv.max_engines),
            allow_deep_memory=adv.allow_deep_memory,
            allow_reasoning=adv.allow_reasoning,
            allow_multimodal=adv.allow_multimodal,
            allow_scenarios=False,
            allow_learning=False,
            llm_model=_MODEL_CHEAP,
            timeout_ms=adv.timeout_ms,
            priority=adv.priority + 2,  # slightly lower priority
        )

    capped_engines = min(base.max_engines, tier_cap)

    return PolicyConfig(
        max_engines=capped_engines,
        allow_deep_memory=base.allow_deep_memory,
        allow_reasoning=base.allow_reasoning,
        allow_multimodal=base.allow_multimodal,
        allow_scenarios=base.allow_scenarios,
        allow_learning=base.allow_learning,
        llm_model=base.llm_model,
        timeout_ms=base.timeout_ms,
        priority=base.priority,
    )
