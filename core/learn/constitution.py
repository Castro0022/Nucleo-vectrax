"""
Vectrax Constitution
=====================
Immutable governance rules for the learning subsystem.

The 10 rules are frozen at load time and cannot be altered at runtime.
All learning operations MUST pass through ``ConstitutionEnforcer``
before any state change.

Key concepts:
  - **Cognitive Consistency (CC)**: per-pattern score [0..1] tracking
    how reliably a pattern has been observed across inputs.
    EMA-based: updated each time the pattern is re-observed.
  - **CC-driven modes**: CC low (<0.4) → OBSERVE only;
    CC medium (0.4–0.75) → PROPOSE; CC high (>0.75) → PROPOSE+"Proceed"
    iff whitelisted + safe.  EXECUTE never emerges from request alone.
  - **Domain separation**: each memory entry is tagged with a domain.
    Only meta-patterns (intent fingerprints) cross domain boundaries.
  - **Dead-end blocking**: patterns that repeatedly fail (CC decays
    below threshold after multiple observations) are blocked early
    to save human and system time.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.learn import VAULT_DIR

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CC_TRACKER_PATH = os.path.join(VAULT_DIR, "cc_tracker.jsonl")
DEAD_ENDS_PATH = os.path.join(VAULT_DIR, "dead_ends.jsonl")

# ---------------------------------------------------------------------------
# The Immutable Constitution (frozen tuple — cannot be mutated)
# ---------------------------------------------------------------------------

CONSTITUTION: Tuple[str, ...] = (
    "Always learn from all inputs; default mode is OBSERVE.",
    "Never execute because requested; execution emerges only from evidence.",
    "Abstract patterns; do not store sensitive raw data.",
    "Separate domain memories; share only meta-patterns across domains.",
    "Track Cognitive Consistency (CC) per pattern.",
    "If CC low: observe + classify only.",
    "If CC medium: propose steps; no execution.",
    "If CC high: propose with Proceed only if whitelisted + safe.",
    "In sensitive contexts: never auto-execute; always require explicit approval.",
    "Optimize human time and system time by blocking known dead-end routes early.",
)

# ---------------------------------------------------------------------------
# CC thresholds
# ---------------------------------------------------------------------------

CC_LOW_THRESHOLD = 0.40     # below → OBSERVE only
CC_MEDIUM_THRESHOLD = 0.75  # below → PROPOSE; above → PROPOSE+Proceed
CC_DECAY_ALPHA = 0.3        # EMA smoothing factor
CC_DEAD_END_THRESHOLD = 0.15  # CC below this after ≥3 observations → dead end
CC_DEAD_END_MIN_OBS = 3     # minimum observations before declaring dead end

# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------

DOMAINS = [
    "core",        # core/ modules
    "pipeline",    # pipeline/ modules
    "cli",         # cli/ modules
    "models",      # models/ schemas
    "services",    # services/ API layer
    "infra",       # setup, config, Makefile, CI
    "tests",       # test modules
    "unknown",
]


def classify_domain(file_path: str) -> str:
    """Classify a file path into a domain."""
    parts = file_path.replace("\\", "/").lower().split("/")
    for segment in parts:
        if segment in ("core",):
            return "core"
        if segment in ("pipeline",):
            return "pipeline"
        if segment in ("cli",):
            return "cli"
        if segment in ("models",):
            return "models"
        if segment in ("services",):
            return "services"
        if segment in ("tests", "test"):
            return "tests"
        if segment in ("setup.py", "config.py", "makefile", ".github"):
            return "infra"
    # Check file name directly
    fname = parts[-1] if parts else ""
    if fname in ("setup.py", "config.py", "pyproject.toml"):
        return "infra"
    if "test" in fname:
        return "tests"
    return "unknown"


# ---------------------------------------------------------------------------
# Cognitive Consistency (CC) Tracker
# ---------------------------------------------------------------------------

@dataclass
class CCEntry:
    """Tracks CC for a single pattern (keyed by intent_fingerprint)."""
    fingerprint: str
    cc_score: float = 0.0
    observations: int = 0
    domain: str = "unknown"
    last_intent: str = ""
    dead_end: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "cc_score": round(self.cc_score, 4),
            "observations": self.observations,
            "domain": self.domain,
            "last_intent": self.last_intent,
            "dead_end": self.dead_end,
        }


class CCTracker:
    """
    Persisted EMA-based Cognitive Consistency tracker.

    CC is updated each time a pattern is re-observed:
      cc_new = α × observation_score + (1 − α) × cc_old

    Where observation_score is the intent confidence from the latest
    observation.
    """

    def __init__(self, path: str = CC_TRACKER_PATH):
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def _load(self) -> Dict[str, CCEntry]:
        entries: Dict[str, CCEntry] = {}
        if not os.path.isfile(self.path):
            return entries
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    entries[d["fingerprint"]] = CCEntry(**d)
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
        return entries

    def _save(self, entries: Dict[str, CCEntry]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            for entry in entries.values():
                f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

    def update(
        self,
        fingerprint: str,
        observation_score: float,
        domain: str = "unknown",
        intent: str = "",
    ) -> CCEntry:
        """
        Update CC for *fingerprint* using EMA.

        First observation initialises CC to *observation_score*.
        Returns the updated CCEntry.
        """
        entries = self._load()
        entry = entries.get(fingerprint)

        if entry is None:
            entry = CCEntry(
                fingerprint=fingerprint,
                cc_score=observation_score,
                observations=1,
                domain=domain,
                last_intent=intent,
            )
        else:
            entry.cc_score = (
                CC_DECAY_ALPHA * observation_score
                + (1.0 - CC_DECAY_ALPHA) * entry.cc_score
            )
            entry.observations += 1
            entry.domain = domain
            entry.last_intent = intent

        # Check dead-end condition
        if (entry.observations >= CC_DEAD_END_MIN_OBS
                and entry.cc_score < CC_DEAD_END_THRESHOLD):
            entry.dead_end = True

        entries[fingerprint] = entry
        self._save(entries)
        return entry

    def get(self, fingerprint: str) -> Optional[CCEntry]:
        return self._load().get(fingerprint)

    def get_cc(self, fingerprint: str) -> float:
        """Return CC score for a fingerprint (0.0 if unseen)."""
        entry = self.get(fingerprint)
        return entry.cc_score if entry else 0.0

    def is_dead_end(self, fingerprint: str) -> bool:
        entry = self.get(fingerprint)
        return entry.dead_end if entry else False

    def all_entries(self) -> List[CCEntry]:
        return list(self._load().values())

    def dead_ends(self) -> List[CCEntry]:
        return [e for e in self.all_entries() if e.dead_end]

    def stats(self) -> Dict[str, Any]:
        entries = self.all_entries()
        if not entries:
            return {"total": 0, "dead_ends": 0, "avg_cc": 0.0}
        return {
            "total": len(entries),
            "dead_ends": sum(1 for e in entries if e.dead_end),
            "avg_cc": round(sum(e.cc_score for e in entries) / len(entries), 4),
            "by_domain": self._count_by_domain(entries),
        }

    @staticmethod
    def _count_by_domain(entries: List[CCEntry]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for e in entries:
            counts[e.domain] = counts.get(e.domain, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# CC-driven Mode Derivation  (Constitution rules 1, 2, 6, 7, 8, 9)
# ---------------------------------------------------------------------------

def derive_mode_from_cc(
    cc_score: float,
    sensitivity: str,
    impact: str,
    is_whitelisted: bool = False,
    is_dead_end: bool = False,
) -> str:
    """
    Derive the learning mode strictly from the Constitution.

    Rules applied:
      Rule 1: default is OBSERVE.
      Rule 2: EXECUTE never emerges from request — only from evidence
              (high CC + whitelisted + safe).  In practice we never
              return EXECUTE; the highest is PROPOSE_PROCEED.
      Rule 6: CC low  → OBSERVE.
      Rule 7: CC medium → PROPOSE.
      Rule 8: CC high → PROPOSE_PROCEED only if whitelisted + safe.
      Rule 9: Sensitive → OBSERVE always.
      Rule 10: Dead end → OBSERVE (block further escalation).

    Returns one of: OBSERVE, PROPOSE, PROPOSE_PROCEED.
    """
    # Rule 10: dead ends are blocked
    if is_dead_end:
        return "OBSERVE"

    # Rule 9: sensitive context
    if sensitivity in ("PII", "SECRETS"):
        return "OBSERVE"

    # Rule 9 extended: high-impact always requires approval
    if impact == "high":
        return "OBSERVE"

    # Rule 6: CC low
    if cc_score < CC_LOW_THRESHOLD:
        return "OBSERVE"

    # Rule 7: CC medium
    if cc_score < CC_MEDIUM_THRESHOLD:
        return "PROPOSE"

    # Rule 8: CC high — propose with "Proceed" only if whitelisted + safe
    if is_whitelisted and impact != "high" and sensitivity == "CLEAN":
        return "PROPOSE_PROCEED"

    # Default: PROPOSE (Rule 2: never auto-execute)
    return "PROPOSE"


# ---------------------------------------------------------------------------
# Constitution Enforcer
# ---------------------------------------------------------------------------

class ConstitutionEnforcer:
    """
    Validates actions against the immutable Constitution.

    Every learning operation MUST call ``enforce()`` before mutating
    state.  The enforcer returns a verdict dict.
    """

    def __init__(self, cc_tracker: Optional[CCTracker] = None):
        self.cc_tracker = cc_tracker or get_cc_tracker()

    def enforce(
        self,
        fingerprint: str,
        sensitivity: str,
        impact: str,
        intent: str = "",
        domain: str = "unknown",
        observation_score: float = 0.0,
        requested_mode: str = "",
    ) -> Dict[str, Any]:
        """
        Enforce all Constitution rules for a learning operation.

        Returns:
            {
                "allowed": bool,
                "mode": str,          # constitutionally-derived mode
                "cc_score": float,
                "is_dead_end": bool,
                "violations": list,   # rule numbers violated (if any)
                "domain": str,
            }
        """
        violations: List[int] = []

        # Update CC (Rule 5)
        cc_entry = self.cc_tracker.update(
            fingerprint=fingerprint,
            observation_score=observation_score,
            domain=domain,
            intent=intent,
        )

        cc_score = cc_entry.cc_score
        is_dead_end = cc_entry.dead_end

        # Rule 3: verify no raw sensitive data is being stored
        # (enforced upstream by scrubber — we just check the flag)
        if sensitivity in ("PII", "SECRETS"):
            violations.append(3)  # warn, but don't block (scrubber handles it)

        # Derive constitutionally-valid mode
        mode = derive_mode_from_cc(
            cc_score=cc_score,
            sensitivity=sensitivity,
            impact=impact,
            is_whitelisted=False,  # no auto-whitelist in learning
            is_dead_end=is_dead_end,
        )

        # Rule 2: if someone requested EXECUTE, override to at most PROPOSE
        if requested_mode == "EXECUTE":
            violations.append(2)
            mode = "OBSERVE"  # hard downgrade

        # Rule 10: dead-end early block
        if is_dead_end:
            violations.append(10)

        allowed = len([v for v in violations if v not in (3,)]) == 0

        return {
            "allowed": allowed,
            "mode": mode,
            "cc_score": round(cc_score, 4),
            "observations": cc_entry.observations,
            "is_dead_end": is_dead_end,
            "violations": violations,
            "domain": domain,
        }

    def validate_immutability(self) -> bool:
        """Verify the constitution has not been tampered with."""
        return len(CONSTITUTION) == 10 and isinstance(CONSTITUTION, tuple)


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_cc_tracker: Optional[CCTracker] = None
_enforcer: Optional[ConstitutionEnforcer] = None


def get_cc_tracker() -> CCTracker:
    global _cc_tracker
    if _cc_tracker is None:
        _cc_tracker = CCTracker()
    return _cc_tracker


def get_enforcer() -> ConstitutionEnforcer:
    global _enforcer
    if _enforcer is None:
        _enforcer = ConstitutionEnforcer()
    return _enforcer
