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
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.learn import VAULT_DIR, RUNTIME_DIR
from core.learn.schemas import GravityRecord, Tier, TIER_ORDER, decimate_history

# Persistent path: ~/.vectrax/ (Docker volume, survives deploys)
# Old path: vault/ (bind-mounted code dir, overwritten by rsync)
GRAVITY_INDEX_PATH = os.path.join(RUNTIME_DIR, "gravity_index.json")
_OLD_GRAVITY_PATH = os.path.join(VAULT_DIR, "gravity_index.json")

# Déjà Vu promotion thresholds
DEJAVU_DEEP_TO_COLD_HITS = 1
DEJAVU_COLD_TO_WARM_HITS = 2
DEJAVU_COLD_TO_WARM_WINDOW_DAYS = 14
DEJAVU_WARM_TO_HOT_HITS = 3
DEJAVU_WARM_TO_HOT_WINDOW_DAYS = 7
DEJAVU_WARM_TO_HOT_MIN_CC = 0.6

MAX_OUTCOME_HISTORY = 20

# Bounded size of GravityRecord.activation_history. Calibrated against a
# real sample (Online Retail II, "Year 2009-2010" sheet, ~513k rows /
# 18,370 stars): p95 of hits-per-star = 154, chosen over p99 (483) as the
# more memory-conservative option — 95% of stars in the sample never need
# decimation at this value; only the top ~5% (mostly genuine bestsellers)
# trigger decimate_history's span-preserving thinning. Global (applies to
# every domain, not just sales_trends) — see
# docs/SALES_TRENDS_CALIBRATION_2026_08_25.md for the full methodology,
# distribution, and an unrelated periodicity-detector fix found along the
# way. Revisit if a future domain's real distribution differs materially.
MAX_ACTIVATION_HISTORY = 154


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str) -> datetime:
    """Parse an ISO-8601 string, normalizing naive datetimes to UTC-aware.

    Vectrax's own clock (_now_iso) never emits naive timestamps, but
    externally supplied historical data (e.g. a dataset export without a
    UTC offset) can. Without this normalization, a naive value stored via
    replay would later crash any comparison against a real, tz-aware
    ``datetime.now(timezone.utc)`` ("can't compare offset-naive and
    offset-aware datetimes").
    """
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_iso_strict(s: str) -> datetime:
    """Like _parse_iso but raises on invalid input instead of masking it,
    and always returns a UTC-aware datetime (naive input is assumed UTC;
    aware input is converted to UTC).

    Used to validate AND normalize caller-supplied ``event_timestamp``
    before trusting it as the effective clock for replay — an invalid
    string must fall back to ``now``, not silently become "now" disguised
    as a parse success. Normalizing here (the input boundary) guarantees
    every ``effective`` timestamp stored in Gravity (first_seen, last_seen,
    activation_history) is UTC-aware, so it can never later collide with a
    naive datetime during comparison.
    """
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Gravity Index
# ---------------------------------------------------------------------------

class GravityIndex:
    """Persisted gravity index — one GravityRecord per fingerprint."""

    def __init__(self, path: str = GRAVITY_INDEX_PATH):
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        # Auto-migrate from old vault/ path if new path is empty
        self._migrate_from_old_path()

    def _migrate_from_old_path(self) -> None:
        """One-time migration: if new path is empty but old path has data, copy it."""
        if self.path != GRAVITY_INDEX_PATH:
            return  # custom path (tests), skip migration
        try:
            new_size = os.path.getsize(self.path) if os.path.isfile(self.path) else 0
            old_size = os.path.getsize(_OLD_GRAVITY_PATH) if os.path.isfile(_OLD_GRAVITY_PATH) else 0
            if old_size > new_size and old_size > 500:  # old has more data
                import shutil
                shutil.copy2(_OLD_GRAVITY_PATH, self.path)
                import logging
                logging.getLogger("vectrax.gravity").info(
                    "MIGRATED gravity_index from vault/ to ~/.vectrax/ (%d bytes)",
                    old_size,
                )
        except Exception:
            pass

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
        # Escritura ATÓMICA (tmp + os.replace): el gravity index se reescribe en
        # cada record_event y es leído concurrentemente por census / observer /
        # provider_affinity. Sin esto, un lector podía ver el archivo truncado a
        # medio escribir → JSONDecodeError y "amnesia" transitoria del universo.
        tmp = f"{self.path}.tmp.{os.getpid()}"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    {k: v.to_dict() for k, v in records.items()},
                    f, indent=2, ensure_ascii=False,
                )
                f.write("\n")
            os.replace(tmp, self.path)
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            raise

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
        event_timestamp: Optional[str] = None,
        **meta: Any,
    ) -> Tuple[GravityRecord, Optional[str]]:
        """
        Register an event.  Returns (record, promotion) where promotion
        is None or the new tier name if Déjà Vu triggered.

        ``event_timestamp`` (optional ISO-8601 string) lets a caller replay
        a historical event with its real occurrence time instead of the
        ingestion time. When omitted, behaviour is identical to before —
        the wall-clock ``now`` is used as the effective clock. When present
        but unparsable, it is ignored and ``now`` is used (never raises).

        Extra metadata kwargs (e.g. ``source="domain_prior"``) are accepted
        and ignored so callers that tag events for provenance — such as
        ``seed_tenant_priors`` — do not raise. ``GravityRecord`` has no slot
        for them; the provenance lives in the fingerprint/summary.
        """
        records = self._load()
        now = _now_iso()
        effective = self._resolve_effective_timestamp(event_timestamp, now)
        # Parsed once, reused as the single "effective clock" for this call
        # (frequency AND Déjà Vu promotion) so no part of Gravity reasons
        # against the real wall-clock while the record itself lives at a
        # replayed historical instant.
        effective_dt = _parse_iso(effective)
        promotion: Optional[str] = None

        rec = records.get(fingerprint)
        if rec is None:
            rec = GravityRecord(
                fingerprint=fingerprint,
                tier=Tier.HOT.value,
                hits=1,
                first_seen=effective,
                last_seen=effective,
                cc_score=cc_score,
                impact=impact,
                domain=domain,
                intent=intent,
                decay_factor=3.0 if impact == "high" else 1.0,
                summary=summary[:200],
            )
        else:
            rec.hits += 1
            # Replay-safe: first_seen/last_seen always bracket every effective
            # timestamp seen so far, regardless of the order events arrive in
            # (real time is monotonic, so this is a no-op for the default
            # "now" path and only matters for historical backfill).
            if _parse_iso(effective) < _parse_iso(rec.first_seen):
                rec.first_seen = effective
            if _parse_iso(effective) > _parse_iso(rec.last_seen):
                rec.last_seen = effective
            rec.cc_score = cc_score
            rec.impact = impact
            rec.domain = domain
            rec.intent = intent
            if impact == "high":
                rec.decay_factor = 3.0
            if summary:
                rec.summary = summary[:200]

            # Déjà Vu promotion — reasoned against the effective clock, not
            # real wall-clock "now" (see effective_dt comment above).
            promotion = self._check_promotion(rec, effective_dt)

        # Update frequency (measured against the effective clock so historical
        # replay does not compute frequency using the real wall-clock "now").
        first = _parse_iso(rec.first_seen)
        elapsed_days = max((effective_dt - first).total_seconds() / 86400, 0.01)
        rec.freq = round(rec.hits / elapsed_days, 4)

        # Outcome history (keep last N)
        rec.outcome_history.append(outcome)
        if len(rec.outcome_history) > MAX_OUTCOME_HISTORY:
            rec.outcome_history = rec.outcome_history[-MAX_OUTCOME_HISTORY:]

        # Activation history: bounded, span-preserving (see decimate_history).
        rec.activation_history.append(effective)
        rec.activation_history = decimate_history(rec.activation_history, MAX_ACTIVATION_HISTORY)

        records[fingerprint] = rec
        self._save(records)
        return rec, promotion

    @staticmethod
    def _resolve_effective_timestamp(event_timestamp: Optional[str], now: str) -> str:
        """Return event_timestamp normalized to a UTC-aware ISO-8601 string,
        or ``now`` if it is missing/unparsable.

        Normalization (not just validation) happens here, at the input
        boundary, so every effective timestamp that reaches first_seen,
        last_seen and activation_history is UTC-aware — regardless of
        whether the caller supplied a naive datetime (e.g. a historical
        dataset export with no UTC offset) or one in another timezone.
        """
        if not event_timestamp:
            return now
        try:
            normalized = _parse_iso_strict(event_timestamp)
            return normalized.isoformat()
        except (ValueError, TypeError):
            return now

    # -- Law 3: Déjà Vu promotion ------------------------------------------

    def _check_promotion(self, rec: GravityRecord, reference_now: datetime) -> Optional[str]:
        """Check if a record qualifies for tier promotion.

        ``reference_now`` is the effective clock for the event just
        recorded (real wall-clock "now" for live events, or the replayed
        historical instant during backfill) — never the real wall-clock
        directly, so a record living in a replayed past does not get its
        Déjà Vu windows evaluated against 2026 while the record itself
        thinks it is 2010. This assumes replay proceeds in non-decreasing
        chronological order; strictly out-of-order backfill can still skew
        window timing (first_seen/last_seen bracketing above remains
        correct regardless).
        """
        tier = Tier(rec.tier)
        now = reference_now
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


    # -- cross-domain queries ------------------------------------------------

    def by_domain(self, domain: str) -> List[GravityRecord]:
        """Return all records in a given domain."""
        return [r for r in self._load().values() if r.domain == domain]

    def domain_stats(self, records: Optional[Dict[str, GravityRecord]] = None) -> Dict[str, Dict[str, Any]]:
        """Aggregate stats per domain (market, cognition, etc.).

        ``records``, if provided, is used instead of re-reading the index
        from disk — lets a caller that already loaded the full index once
        (e.g. alongside ``all_records()``) avoid paying the disk read +
        JSON parse cost again for a large index. Defaults to ``self._load()``
        for full backward compatibility.
        """
        domains: Dict[str, list] = {}
        for rec in (records if records is not None else self._load()).values():
            domains.setdefault(rec.domain, []).append(rec)
        result = {}
        for d, recs in domains.items():
            cc_vals = [r.cc_score for r in recs]
            freq_vals = [r.freq for r in recs]
            tiers = {}
            for r in recs:
                tiers[r.tier] = tiers.get(r.tier, 0) + 1
            result[d] = {
                "count": len(recs),
                "avg_cc": round(statistics.mean(cc_vals), 4) if cc_vals else 0,
                "avg_freq": round(statistics.mean(freq_vals), 4) if freq_vals else 0,
                "total_hits": sum(r.hits for r in recs),
                "tiers": tiers,
            }
        return result

    def cross_domain_convergences(
        self,
        domain_a: str = "market",
        domain_b: str = "",
        min_cc: float = 0.0,
        records: Optional[Dict[str, GravityRecord]] = None,
    ) -> List[Dict[str, Any]]:
        """Find convergences between two domains.

        A convergence is detected when stars from different domains
        share the same intent or have overlapping temporal activity.
        If domain_b is empty, matches against ALL non-domain_a stars.

        ``records``, if provided, is used instead of re-reading the index
        from disk (see ``domain_stats`` docstring — same rationale: avoid
        redundant full-index reloads when a caller already has one).
        """
        records = records if records is not None else self._load()
        a_recs = [r for r in records.values() if r.domain == domain_a]
        b_recs = [
            r for r in records.values()
            if (r.domain == domain_b if domain_b else r.domain != domain_a)
        ]

        convergences = []
        for a in a_recs:
            for b in b_recs:
                # Intent overlap: same intent keyword
                if a.intent and b.intent and (
                    a.intent.lower() in b.intent.lower()
                    or b.intent.lower() in a.intent.lower()
                ):
                    combined_cc = (a.cc_score + b.cc_score) / 2
                    if combined_cc >= min_cc:
                        convergences.append({
                            "type": "intent_overlap",
                            "star_a": a.fingerprint,
                            "star_b": b.fingerprint,
                            "intent": a.intent,
                            "combined_cc": round(combined_cc, 4),
                            "combined_hits": a.hits + b.hits,
                            "domains": [domain_a, domain_b],
                        })
                        continue

                # Temporal proximity: both active in last 7 days AND
                # both have meaningful coherence (avoids noise)
                a_last = _parse_iso(a.last_seen)
                b_last = _parse_iso(b.last_seen)
                now = datetime.now(timezone.utc)
                both_recent = (
                    (now - a_last).total_seconds() < 7 * 86400
                    and (now - b_last).total_seconds() < 7 * 86400
                )
                combined_cc_tp = (a.cc_score + b.cc_score) / 2
                if both_recent and combined_cc_tp >= 0.3:
                    convergences.append({
                        "type": "temporal_proximity",
                        "star_a": a.fingerprint,
                        "star_b": b.fingerprint,
                        "a_intent": a.intent,
                        "b_intent": b.intent,
                        "combined_cc": round(combined_cc_tp, 4),
                        "domains": [domain_a, domain_b],
                    })

        return convergences

    def universe_summary(self) -> Dict[str, Any]:
        """Full universe snapshot: domains, tiers, convergences."""
        records = self.all_records()
        domains = self.domain_stats()
        tiers = self.tier_counts()
        market_cognitive = self.cross_domain_convergences()
        return {
            "total_stars": len(records),
            "tiers": tiers,
            "domains": domains,
            "cross_domain_convergences": len(market_cognitive),
            "convergence_details": market_cognitive[:10],
        }


    def top_stars(
        self,
        n: int = 10,
        domain: Optional[str] = None,
    ) -> List[GravityRecord]:
        """Return the top N stars by gravitational weight (hits × cc × freq)."""
        recs = self.by_domain(domain) if domain else self.all_records()
        for r in recs:
            r._weight = r.hits * max(r.cc_score, 0.01) * max(r.freq, 0.01) * r.decay_factor
        return sorted(recs, key=lambda r: r._weight, reverse=True)[:n]

    def growth_trends(self, days: int = 7) -> Dict[str, Any]:
        """Analyze growth trends over a time window."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        records = self._load()
        new_stars = [r for r in records.values()
                     if _parse_iso(r.first_seen) >= cutoff]
        active_stars = [r for r in records.values()
                        if _parse_iso(r.last_seen) >= cutoff]
        growing = [r for r in active_stars if r.hits >= 3]

        # Domain breakdown of new stars
        new_by_domain: Dict[str, int] = {}
        for r in new_stars:
            new_by_domain[r.domain] = new_by_domain.get(r.domain, 0) + 1

        return {
            "window_days": days,
            "new_stars": len(new_stars),
            "active_stars": len(active_stars),
            "growing_stars": len(growing),
            "total_stars": len(records),
            "new_by_domain": new_by_domain,
            "top_growing": sorted(
                growing, key=lambda r: r.freq, reverse=True,
            )[:5],
        }


# ---------------------------------------------------------------------------
# Convergence alerts
# ---------------------------------------------------------------------------

# Thresholds for triggering alerts
ALERT_MIN_HITS = 5        # combined hits to consider significant
ALERT_MIN_CC = 0.4        # combined coherence to consider strong
ALERT_GROWTH_FACTOR = 2   # hits doubled since last check

_ALERTS_SENT_FILE = os.path.join(RUNTIME_DIR, "convergence_alerts_sent.json")


def _load_sent_alerts() -> Dict[str, Any]:
    try:
        if os.path.isfile(_ALERTS_SENT_FILE):
            with open(_ALERTS_SENT_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_sent_alerts(sent: Dict[str, Any]) -> None:
    try:
        with open(_ALERTS_SENT_FILE, "w") as f:
            json.dump(sent, f, indent=2)
    except Exception:
        pass


def check_convergence_alerts() -> List[Dict[str, Any]]:
    """Scan for convergences that exceed critical thresholds.

    Returns list of new alerts (not previously sent).
    Each alert includes the convergence detail + reason.
    """
    gi = get_gravity_index()
    convergences = gi.cross_domain_convergences()
    sent = _load_sent_alerts()
    new_alerts = []

    for conv in convergences:
        # Build a stable key for deduplication
        key = f"{conv['star_a']}:{conv['star_b']}"
        prev = sent.get(key, {})
        prev_hits = prev.get("hits", 0)

        hits = conv.get("combined_hits", 0)
        cc = conv.get("combined_cc", 0)
        reasons = []

        # Check thresholds
        if hits >= ALERT_MIN_HITS and prev_hits < ALERT_MIN_HITS:
            reasons.append(f"hits={hits} superó umbral ({ALERT_MIN_HITS})")

        if cc >= ALERT_MIN_CC and prev.get("cc", 0) < ALERT_MIN_CC:
            reasons.append(f"coherencia={cc:.2f} superó umbral ({ALERT_MIN_CC})")

        if prev_hits > 0 and hits >= prev_hits * ALERT_GROWTH_FACTOR:
            reasons.append(f"hits x{hits / max(prev_hits, 1):.1f} desde última alerta")

        if reasons:
            alert = {
                **conv,
                "alert_reasons": reasons,
                "key": key,
            }
            new_alerts.append(alert)
            sent[key] = {"hits": hits, "cc": cc, "alerted_at": time.time()}

    if new_alerts:
        _save_sent_alerts(sent)

    return new_alerts


_ALERT_HISTORY_FILE = os.path.join(RUNTIME_DIR, "convergence_alert_history.jsonl")
_MAX_ALERT_HISTORY = 200


def _append_alert_history(alert: Dict[str, Any]) -> None:
    """Append alert to persistent JSONL history."""
    hist_file = _ALERT_HISTORY_FILE
    try:
        os.makedirs(os.path.dirname(hist_file), exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ts": time.time(),
            "star_a": alert.get("star_a", ""),
            "star_b": alert.get("star_b", ""),
            "intent": alert.get("intent", alert.get("a_intent", "")),
            "type": alert.get("type", ""),
            "combined_hits": alert.get("combined_hits", 0),
            "combined_cc": alert.get("combined_cc", 0),
            "domains": alert.get("domains", []),
            "reasons": alert.get("alert_reasons", []),
        }
        with open(hist_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _trim_alert_history() -> None:
    try:
        with open(_ALERT_HISTORY_FILE) as f:
            lines = f.readlines()
        if len(lines) > _MAX_ALERT_HISTORY:
            with open(_ALERT_HISTORY_FILE, "w") as f:
                f.writelines(lines[-_MAX_ALERT_HISTORY:])
    except Exception:
        pass


def get_alert_history(limit: int = 20) -> List[Dict[str, Any]]:
    """Return recent alert history (newest first)."""
    hist_file = _ALERT_HISTORY_FILE
    if not os.path.isfile(hist_file):
        return []
    entries = []
    try:
        with open(hist_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception:
        pass
    return list(reversed(entries[-limit:]))


def format_alert_history(limit: int = 10) -> str:
    """Format alert history for Telegram."""
    alerts = get_alert_history(limit)
    if not alerts:
        return "Sin alertas de convergencia registradas."

    lines = [f"\u26a1 <b>Historial de alertas ({len(alerts)} recientes)</b>\n"]
    for a in alerts:
        ts = a.get("timestamp", "")[:16]
        intent = a.get("intent", "?")
        hits = a.get("combined_hits", 0)
        cc = a.get("combined_cc", 0)
        reasons = ", ".join(a.get("reasons", []))
        lines.append(
            f"[{ts}] <b>{intent}</b>\n"
            f"  {a.get('star_a', '')} \u2194 {a.get('star_b', '')}\n"
            f"  hits={hits} cc={cc} | {reasons}"
        )

    return "\n".join(lines)


def send_convergence_alerts() -> int:
    """Check for critical convergences and record in history.

    DISABLED: Telegram notifications turned off by creator request.
    Convergence data is still recorded in alert_history for analysis
    and available via /vx market alerts. No messages sent to chat.

    Returns number of alerts recorded (not sent).
    """
    alerts = check_convergence_alerts()
    if not alerts:
        return 0

    # Record in history file (available via get_alert_history())
    for alert in alerts:
        _append_alert_history(alert)

    # NO Telegram notification — alerts are silent by default.
    # Data is preserved in vault/convergence_alert_history.jsonl.
    # Creator can check via /vx market alerts when needed.

    return 0  # 0 = no messages sent to Telegram


def universe_report(lang: str = "es") -> str:
    """Generate a human-readable universe report for Telegram / self-context."""
    gi = get_gravity_index()
    summary = gi.universe_summary()
    trends = gi.growth_trends(days=7)
    top = gi.top_stars(n=8)
    convergences = summary.get("convergence_details", [])

    lines = []
    if lang == "es":
        lines.append("🌌 <b>Universo Gravitacional — Reporte</b>")
    else:
        lines.append("🌌 <b>Gravitational Universe — Report</b>")

    # Overview
    total = summary["total_stars"]
    tiers = summary["tiers"]
    lines.append(f"")
    lines.append(f"⭐ {total} estrellas totales")
    lines.append(
        f"   HOT={tiers.get('HOT', 0)} | WARM={tiers.get('WARM', 0)} | "
        f"COLD={tiers.get('COLD', 0)} | DEEP={tiers.get('DEEP', 0)}"
    )

    # Domains
    lines.append("")
    lines.append("<b>Dominios:</b>")
    for d, stats in summary["domains"].items():
        lines.append(
            f"  {d}: {stats['count']} ⭐ | cc={stats['avg_cc']} | "
            f"hits={stats['total_hits']}"
        )

    # Top stars by gravitational weight
    lines.append("")
    lines.append("<b>Estrellas con mayor masa gravitacional:</b>")
    for i, r in enumerate(top, 1):
        weight = round(r.hits * max(r.cc_score, 0.01) * max(r.freq, 0.01) * r.decay_factor, 2)
        lines.append(
            f"  {i}. {r.fingerprint[:25]} [{r.domain}]\n"
            f"     hits={r.hits} cc={r.cc_score:.2f} freq={r.freq:.2f} "
            f"peso={weight}"
        )

    # Growth trends
    lines.append("")
    lines.append(f"<b>Tendencia (últimos {trends['window_days']}d):</b>")
    lines.append(
        f"  Nuevas: {trends['new_stars']} | "
        f"Activas: {trends['active_stars']} | "
        f"Creciendo: {trends['growing_stars']}"
    )
    if trends["new_by_domain"]:
        parts = [f"{d}={n}" for d, n in trends["new_by_domain"].items()]
        lines.append(f"  Nuevas por dominio: {', '.join(parts)}")

    if trends["top_growing"]:
        lines.append("  Más activas:")
        for r in trends["top_growing"]:
            lines.append(
                f"    📈 {r.fingerprint[:25]} freq={r.freq:.2f} hits={r.hits}"
            )

    # Cross-domain convergences
    intent_convs = [c for c in convergences if c["type"] == "intent_overlap"]
    if intent_convs:
        lines.append("")
        lines.append(f"<b>Convergencias cross-domain: {len(intent_convs)}</b>")
        for c in intent_convs:
            lines.append(
                f"  🔗 {c['star_a']} ↔ {c['star_b']}\n"
                f"     symbol={c['intent']} cc={c['combined_cc']} "
                f"hits={c['combined_hits']}"
            )
    else:
        lines.append("")
        lines.append("Convergencias cross-domain: 0 (se forman con observación continua)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_index: Optional[GravityIndex] = None


def get_gravity_index() -> GravityIndex:
    global _index
    if _index is None:
        _index = GravityIndex()
    return _index
