"""
Vectrax Intent Engine
======================
Keyword/heuristic classifier — no LLM dependency.

Taxonomy (10 intents):
  FEATURE_ADD, BUG_FIX, REFACTOR, TEST, CONFIG, DOCS,
  SECURITY, PERF, INFRA, UNKNOWN

Returns intent_primary, intent_secondary, confidence, sensitivity, impact,
and the derived learning mode (OBSERVE / PROPOSE / EXECUTE).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from core.learn.scrubber import Sensitivity, scrub


# ---------------------------------------------------------------------------
# Keyword maps  (intent -> weighted keywords)
# ---------------------------------------------------------------------------

_KEYWORD_MAP: Dict[str, List[Tuple[str, float]]] = {
    "FEATURE_ADD": [
        ("feature", 1.0), ("add", 0.6), ("new", 0.5), ("implement", 0.8),
        ("create", 0.6), ("introduce", 0.7), ("enable", 0.5),
    ],
    "BUG_FIX": [
        ("fix", 1.0), ("bug", 1.0), ("patch", 0.8), ("repair", 0.7),
        ("resolve", 0.7), ("issue", 0.5), ("error", 0.6), ("crash", 0.8),
    ],
    "REFACTOR": [
        ("refactor", 1.0), ("rename", 0.7), ("restructure", 0.8),
        ("simplify", 0.6), ("clean", 0.5), ("move", 0.4), ("extract", 0.6),
    ],
    "TEST": [
        ("test", 1.0), ("assert", 0.8), ("unittest", 0.9), ("pytest", 0.9),
        ("mock", 0.7), ("coverage", 0.6), ("fixture", 0.7),
    ],
    "CONFIG": [
        ("config", 1.0), ("setting", 0.8), ("env", 0.6), ("threshold", 0.5),
        ("parameter", 0.5), ("yaml", 0.6), ("toml", 0.6), (".env", 0.7),
    ],
    "DOCS": [
        ("doc", 1.0), ("readme", 0.9), ("comment", 0.6), ("docstring", 0.8),
        ("documentation", 1.0), ("explain", 0.5), ("changelog", 0.7),
    ],
    "SECURITY": [
        ("security", 1.0), ("auth", 0.8), ("token", 0.6), ("permission", 0.7),
        ("encrypt", 0.9), ("credential", 0.8), ("secret", 0.7), ("vulnerability", 1.0),
    ],
    "PERF": [
        ("performance", 1.0), ("optimize", 0.9), ("speed", 0.7), ("cache", 0.7),
        ("latency", 0.8), ("throughput", 0.7), ("bottleneck", 0.8), ("fast", 0.4),
    ],
    "INFRA": [
        ("infra", 1.0), ("deploy", 0.9), ("ci", 0.7), ("cd", 0.6),
        ("docker", 0.8), ("kubernetes", 0.8), ("pipeline", 0.5),
        ("makefile", 0.6), ("setup", 0.5), ("install", 0.5),
    ],
}

# Impact defaults per intent
_IMPACT_MAP: Dict[str, str] = {
    "FEATURE_ADD": "medium",
    "BUG_FIX": "medium",
    "REFACTOR": "medium",
    "TEST": "low",
    "CONFIG": "low",
    "DOCS": "low",
    "SECURITY": "high",
    "PERF": "medium",
    "INFRA": "high",
    "UNKNOWN": "low",
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class IntentResult:
    intent_primary: str
    intent_secondary: Optional[str]
    confidence: float          # 0..1
    sensitivity: str           # CLEAN / PII / SECRETS
    impact: str                # low / medium / high
    mode: str                  # OBSERVE / PROPOSE / PROPOSE_PROCEED
    fingerprint: str           # deterministic hash of intent+confidence
    cc_score: float = 0.0      # Cognitive Consistency score
    domain: str = "unknown"    # Domain classification
    scores: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "intent_primary": self.intent_primary,
            "intent_secondary": self.intent_secondary,
            "confidence": round(self.confidence, 4),
            "sensitivity": self.sensitivity,
            "impact": self.impact,
            "mode": self.mode,
            "fingerprint": self.fingerprint,
            "cc_score": round(self.cc_score, 4),
            "domain": self.domain,
        }


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

def _score_text(text: str) -> Dict[str, float]:
    """Score *text* against each intent's keyword list."""
    text_lower = text.lower()
    scores: Dict[str, float] = {}
    for intent, keywords in _KEYWORD_MAP.items():
        total = 0.0
        for kw, weight in keywords:
            # Count occurrences (capped at 3 to avoid over-counting)
            count = min(len(re.findall(re.escape(kw), text_lower)), 3)
            total += count * weight
        scores[intent] = total
    return scores


def _derive_mode(impact: str, sensitivity: str, cc_score: float = 0.0) -> str:
    """
    Constitution-driven mode derivation.

    Delegates to the Constitution's CC-driven logic when cc_score is
    available, falls back to conservative defaults otherwise.
    """
    try:
        from core.learn.constitution import derive_mode_from_cc
        return derive_mode_from_cc(
            cc_score=cc_score,
            sensitivity=sensitivity,
            impact=impact,
        )
    except ImportError:
        pass
    # Fallback (pre-constitution behaviour)
    if sensitivity in (Sensitivity.PII.value, Sensitivity.SECRETS.value):
        return "OBSERVE"
    if impact == "high":
        return "OBSERVE"
    if impact == "medium":
        return "PROPOSE"
    return "OBSERVE"


def _fingerprint(primary: str, confidence: float) -> str:
    raw = f"{primary}:{confidence:.4f}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def infer_intent(text: str, file_path: str = "") -> IntentResult:
    """
    Infer intent from source *text* (file content or diff).

    Args:
        text: The text to classify.
        file_path: Optional path for extra heuristic signals.

    Returns:
        IntentResult with primary/secondary intent, confidence, etc.
    """
    # Scrub first to determine sensitivity
    sr = scrub(text)
    sensitivity = sr.sensitivity.value

    # Score
    combined = text + " " + file_path
    scores = _score_text(combined)

    # Sort by score descending
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best_score = ranked[0][1] if ranked else 0.0
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0

    # Determine primary/secondary
    if best_score == 0.0:
        primary = "UNKNOWN"
        secondary = None
        confidence = 0.1
    else:
        primary = ranked[0][0]
        secondary = ranked[1][0] if second_score > 0.0 else None
        # Confidence: normalised score (cap at 1.0)
        max_possible = sum(w for _, w in _KEYWORD_MAP[primary]) * 3  # max 3 counts
        confidence = min(best_score / max(max_possible, 1.0), 1.0)
        # Boost if dominant
        if second_score > 0 and best_score / second_score > 2.0:
            confidence = min(confidence + 0.1, 1.0)

    # Path-based heuristics
    if file_path:
        pl = file_path.lower()
        if "test" in pl and primary != "TEST":
            secondary = primary if secondary is None else secondary
            primary = "TEST"
            confidence = max(confidence, 0.6)
        elif "doc" in pl or "readme" in pl:
            if primary != "DOCS":
                secondary = primary
                primary = "DOCS"
                confidence = max(confidence, 0.5)

    impact = _IMPACT_MAP.get(primary, "low")
    fp = _fingerprint(primary, confidence)

    # Domain classification
    domain = "unknown"
    try:
        from core.learn.constitution import classify_domain
        domain = classify_domain(file_path) if file_path else "unknown"
    except ImportError:
        pass

    # CC-aware mode derivation
    cc_score = 0.0
    try:
        from core.learn.constitution import get_cc_tracker
        cc_score = get_cc_tracker().get_cc(fp)
    except ImportError:
        pass

    mode = _derive_mode(impact, sensitivity, cc_score=cc_score)

    return IntentResult(
        intent_primary=primary,
        intent_secondary=secondary,
        confidence=confidence,
        sensitivity=sensitivity,
        impact=impact,
        mode=mode,
        fingerprint=fp,
        cc_score=cc_score,
        domain=domain,
        scores=scores,
    )
