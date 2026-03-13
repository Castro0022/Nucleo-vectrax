"""
Vectrax Gravity Engine
=======================
Layered memory: HOT → WARM → COLD → DEEP.  Nothing is ever deleted.

Law 1 — Absolute Registration: every event enters the gravity index.
Law 3 — Déjà Vu: reappearing patterns promote to hotter tiers.
Law 4 — No deletion: only cool / compress / archive.

Persistence: ``vault/gravity_index.json``
"""

from __future__ import annotations

import json
import os
import statistics
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.learn import VAULT_DIR
from core.learn.schemas import GravityRecord, Tier, TIER_ORDER

GRAVITY_INDEX_PATH = os.path.join(VAULT_DIR, "gravity_index.json")

# Déjà Vu promotion thresholds
DEJAVU_DEEP_TO_COLD_HITS = 1
DEJAVU_COLD_TO_WARM_HITS = 2
DEJAVU_COLD_TO_WARM_WINDOW_DAYS = 14
DEJAVU_WARM_TO_HOT_HITS = 3
DEJAVU_WARM_TO_HOT_WINDOW_DAYS = 7
DEJAVU_WARM_TO_HOT_MIN_CC = 0.6

MAX_OUTCOME_HISTORY = 20


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Gravity Index
# ---------------------------------------------------------------------------

class GravityIndex:
    """Persisted gravity index — one GravityRecord per fingerprint."""

    def __init__(self, path: str = GRAVITY_INDEX_PATH):
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    # -- persistence --------------------------------------------------------

    def _load(self) -> Dict[str, GravityRecord]:
        if not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {k: GravityRecord.from_dict(v) for k, v in data.items()}
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, records: Dict[str, GravityRecord]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(
                {k: v.to_dict() for k, v in records.items()},
                f, indent=2, ensure_ascii=False,
            )
            f.write("\n")

    # -- Law 1: absolute registration ---------------------------------------

    def record_event(
        self,
        fingerprint: str,
        cc_score: float = 0.0,
        impact: str = "low",
        domain: str = "unknown",
        intent: str = "",
        outcome: str = "observed",
        summary: str = "",
    ) -> Tuple[GravityRecord, Optional[str]]:
        """
        Register an event.  Returns (record, promotion) where promotion
        is None or the new tier name if Déjà Vu triggered.
        """
        records = self._load()
        now = _now_iso()
        promotion: Optional[str] = None

        rec = records.get(fingerprint)
        if rec is None:
            rec = GravityRecord(
                fingerprint=fingerprint,
                tier=Tier.HOT.value,
                hits=1,
                first_seen=now,
                last_seen=now,
                cc_score=cc_score,
                impact=impact,
                domain=domain,
                intent=intent,
                decay_factor=3.0 if impact == "high" else 1.0,
                summary=summary[:200],
            )
        else:
            rec.hits += 1
            rec.last_seen = now
            rec.cc_score = cc_score
            rec.impact = impact
            rec.domain = domain
            rec.intent = intent
            if impact == "high":
                rec.decay_factor = 3.0
            if summary:
                rec.summary = summary[:200]

            # Déjà Vu promotion
            promotion = self._check_promotion(rec)

        # Update frequency
        first = _parse_iso(rec.first_seen)
        elapsed_days = max((datetime.now(timezone.utc) - first).total_seconds() / 86400, 0.01)
        rec.freq = round(rec.hits / elapsed_days, 4)

        # Outcome history (keep last N)
        rec.outcome_history.append(outcome)
        if len(rec.outcome_history) > MAX_OUTCOME_HISTORY:
            rec.outcome_history = rec.outcome_history[-MAX_OUTCOME_HISTORY:]

        records[fingerprint] = rec
        self._save(records)
        return rec, promotion

    # -- Law 3: Déjà Vu promotion ------------------------------------------

    def _check_promotion(self, rec: GravityRecord) -> Optional[str]:
        """Check if a record qualifies for tier promotion."""
        tier = Tier(rec.tier)
        now = datetime.now(timezone.utc)
        last = _parse_iso(rec.last_seen)

        if tier == Tier.DEEP:
            if rec.hits >= DEJAVU_DEEP_TO_COLD_HITS:
                rec.tier = Tier.COLD.value
                return Tier.COLD.value

        elif tier == Tier.COLD:
            first = _parse_iso(rec.first_seen)
            # Count recent hits within window
            window = timedelta(days=DEJAVU_COLD_TO_WARM_WINDOW_DAYS)
            if rec.hits >= DEJAVU_COLD_TO_WARM_HITS and (now - last) < window:
                rec.tier = Tier.WARM.value
                return Tier.WARM.value

        elif tier == Tier.WARM:
            window = timedelta(days=DEJAVU_WARM_TO_HOT_WINDOW_DAYS)
            if (rec.hits >= DEJAVU_WARM_TO_HOT_HITS
                    and (now - last) < window
                    and rec.cc_score >= DEJAVU_WARM_TO_HOT_MIN_CC):
                rec.tier = Tier.HOT.value
                return Tier.HOT.value

        return None

    # -- queries ------------------------------------------------------------

    def get(self, fingerprint: str) -> Optional[GravityRecord]:
        return self._load().get(fingerprint)

    def get_tier(self, fingerprint: str) -> Optional[str]:
        rec = self.get(fingerprint)
        return rec.tier if rec else None

    def search_by_tier(
        self, tiers: Optional[List[str]] = None
    ) -> Dict[str, List[GravityRecord]]:
        """Group records by tier.  If tiers given, filter to those."""
        records = self._load()
        result: Dict[str, List[GravityRecord]] = {t.value: [] for t in TIER_ORDER}
        for rec in records.values():
            if tiers is None or rec.tier in tiers:
                result.setdefault(rec.tier, []).append(rec)
        return result

    def search_similar(self, fingerprint: str) -> List[GravityRecord]:
        """Find records sharing the same intent prefix (first 6 chars)."""
        prefix = fingerprint[:6]
        return [
            r for r in self._load().values()
            if r.fingerprint[:6] == prefix
        ]

    def tier_counts(self) -> Dict[str, int]:
        counts = {t.value: 0 for t in TIER_ORDER}
        for rec in self._load().values():
            counts[rec.tier] = counts.get(rec.tier, 0) + 1
        return counts

    def tier_stats(self) -> Dict[str, Any]:
        """Summary stats per tier: count, avg_cc, avg_freq."""
        by_tier = self.search_by_tier()
        stats = {}
        for tier_name, recs in by_tier.items():
            if not recs:
                stats[tier_name] = {"count": 0, "avg_cc": 0.0, "avg_freq": 0.0}
                continue
            cc_vals = [r.cc_score for r in recs]
            freq_vals = [r.freq for r in recs]
            stats[tier_name] = {
                "count": len(recs),
                "avg_cc": round(statistics.mean(cc_vals), 4),
                "avg_freq": round(statistics.mean(freq_vals), 4),
            }
        return stats

    def all_records(self) -> List[GravityRecord]:
        return list(self._load().values())

    def update_records(self, records: Dict[str, GravityRecord]) -> None:
        """Bulk update (used by decay engine)."""
        self._save(records)

    def load_raw(self) -> Dict[str, GravityRecord]:
        """Expose raw load for decay/constellation."""
        return self._load()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_index: Optional[GravityIndex] = None


def get_gravity_index() -> GravityIndex:
    global _index
    if _index is None:
        _index = GravityIndex()
    return _index
