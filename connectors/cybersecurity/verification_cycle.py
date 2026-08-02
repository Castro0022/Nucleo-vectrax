"""
connectors/cybersecurity/verification_cycle.py — Cierre VERIFICADO del dominio.

Resuelve cada CVE a su outcome (WIN/timing | LOSS | PENDING | NEUTRAL) vía
CyberOutcomeAdapter y lo registra en el verification_ledger genérico a NIVEL de
subject (family/class/vector/tier de la escalera). Idempotente por `cve_id`
usando el seen-ledger: solo escribe cuando la CVE es nueva o cambió (is_new /
is_changed), de modo que re-ejecutar el backfill añade 0 líneas.

El único flip de un decisivo ya escrito es LOSS→WIN (CVE vieja no-KEV que luego
entra a KEV). Se escribe un WIN superseding y la LECTURA (`verified_subjects` /
`verified_score`) deduplica por `prediction_id` quedándose con el `resolved_ts`
más reciente — el `verification_ledger` del core queda intacto (dedupe aditivo).

Exclusión de alcance: CVE publicadas antes de 2020-01-01 NO se materializan.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from core.learn.outcome_adapter import (
    DomainScore,
    Outcome,
    OutcomeStatus,
    Prediction,
    score_outcomes,
)
from core.learn import verification_ledger as vledger
from connectors.cybersecurity import seen_ledger
from connectors.cybersecurity.base import DOMAIN
from connectors.cybersecurity.cve_outcome_adapter import CyberOutcomeAdapter
from connectors.cybersecurity.dimensions import extract_dims, has_any_dim, subject_ladder

logger = logging.getLogger("vectrax.cybersecurity.verification_cycle")

_ADAPTER = CyberOutcomeAdapter()
MIN_PUB = datetime(2020, 1, 1, tzinfo=timezone.utc)  # alcance histórico inicial


def _parse_iso(s: Any) -> Optional[datetime]:
    if not s:
        return None
    txt = str(s).strip()
    if txt.endswith("Z"):
        txt = txt[:-1]
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(txt, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(txt)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def resolve_cve(ev: Any, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Calcula dims, escalera, hechos temporales y el Outcome (a nivel CVE)."""
    now_dt = now or datetime.now(timezone.utc)
    data = getattr(ev, "data", None) or (ev if isinstance(ev, Mapping) else {})
    cid = str(data.get("cve_id") or "").strip().upper()

    pub = _parse_iso(data.get("published"))
    excluded = (pub is None) or (pub < MIN_PUB)

    dims = extract_dims(data)
    dims_present = has_any_dim(dims)
    in_kev = bool(data.get("in_kev"))
    kev_dt = _parse_iso(data.get("kev_date")) if in_kev else None

    days_to_kev = None
    if in_kev and pub and kev_dt:
        days_to_kev = max(0, (kev_dt - pub).days)
    age_days = (now_dt - pub).days if pub else None

    subjects = subject_ladder(dims)
    obs = {"in_kev": in_kev, "days_to_kev": days_to_kev,
           "age_days": age_days, "has_dims": dims_present}
    pred = Prediction(
        domain=DOMAIN,
        subject=(subjects[0][1] if subjects else f"cve:{cid}"),
        predicted="exploited", prediction_id=cid,
    )
    outcome = _ADAPTER.resolve(pred, obs)

    seen_rec = {
        "cve_id": cid,
        "published_ts": pub.timestamp() if pub else 0.0,
        "in_kev": int(in_kev),
        "kev_date_ts": kev_dt.timestamp() if kev_dt else 0.0,
        "days_to_kev": days_to_kev,
        "product_family": dims.get("product_family"),
        "vulnerability_class": dims.get("vulnerability_class"),
        "attack_vector": dims.get("attack_vector"),
        "severity_tier": dims.get("severity_tier"),
        "outcome": outcome.status.value,
        "timing": outcome.evidence.get("timing"),
        "state": outcome.status.value,
    }
    return {
        "cve_id": cid, "excluded": excluded, "dims": dims,
        "subjects": subjects, "outcome": outcome, "seen_rec": seen_rec,
    }


def verify_events(events: Iterable[Any], record: bool = True,
                  now: Optional[datetime] = None,
                  gravity_records: Optional[Dict[str, Any]] = None) -> DomainScore:
    """Resuelve un lote y (si record) persiste los decisivos por nivel de subject,
    de forma idempotente. Devuelve el DomainScore a nivel CVE del lote.

    Si `gravity_records` (dict fingerprint→GravityRecord) se provee, acumula masa
    de estrellas SOLO en la primera vez que se ve la CVE (is_new) — el caller
    (backfill) persiste el dict una vez por ventana. La masa NO se re-incrementa
    en re-ejecuciones ni en flips (is_changed)."""
    cve_outcomes: List[Outcome] = []
    for ev in events:
        r = resolve_cve(ev, now)
        if r["excluded"] or not r["cve_id"]:
            continue
        is_new, is_changed = seen_ledger.upsert(r["seen_rec"])
        o = r["outcome"]
        cve_outcomes.append(o)  # nivel CVE (subject = nivel más fino o cve:id)

        if record and o.status in (OutcomeStatus.WIN, OutcomeStatus.LOSS) and (is_new or is_changed):
            for level, subject_key in r["subjects"]:
                vledger.record_outcome(Outcome(
                    prediction_id=f"{r['cve_id']}|{level}",
                    domain=DOMAIN, subject=subject_key,
                    status=o.status, score=o.score, evidence=o.evidence,
                ))
        if gravity_records is not None and is_new:
            _accumulate_mass(gravity_records, r["dims"])
    score = score_outcomes(DOMAIN, cve_outcomes)
    logger.info(
        "cyber.verification | batch=%d | dec=%d | WR=%.0f%% | wins=%d losses=%d neutral=%d pending=%d",
        score.n_total, score.n_decisive, score.win_rate,
        score.wins, score.losses, score.neutrals, score.pending,
    )
    return score


# ── Masa de estrellas (bulk, sin reescribir el índice por evento) ───────────────

_CC_BY_LEVEL = {"family": 0.6, "class": 0.5, "vector": 0.4, "tier": 0.4}


def _accumulate_mass(records: Dict[str, Any], dims: Mapping[str, Optional[str]]) -> None:
    """Incrementa (o crea) la estrella gravitacional de cada nivel presente, en
    memoria. Fingerprint idéntico al que produciría `ingest_event` con el template
    (`cybersecurity:cve_<level>:<field>=<valor>`) → paridad de identidad."""
    from core.learn.schemas import GravityRecord, Tier  # lazy
    from connectors.cybersecurity.dimensions import LADDER, LEVEL_EVENT
    now_iso = datetime.now(timezone.utc).isoformat()
    for level in LADDER:
        event_type, field_name = LEVEL_EVENT[level]
        val = dims.get(field_name)
        if not val:
            continue
        fp = f"{DOMAIN}:{event_type}:{field_name}={val}"
        rec = records.get(fp)
        if rec is None:
            rec = GravityRecord(
                fingerprint=fp, tier=Tier.HOT.value, hits=1,
                first_seen=now_iso, last_seen=now_iso,
                cc_score=_CC_BY_LEVEL.get(level, 0.4), impact="medium",
                domain=DOMAIN, intent=str(val), decay_factor=1.0,
                summary=f"cve {field_name}={val}"[:200],
            )
            records[fp] = rec
        else:
            rec.hits += 1
            rec.last_seen = now_iso
            rec.domain = DOMAIN
        try:
            first = datetime.fromisoformat(rec.first_seen)
            elapsed = max((datetime.now(timezone.utc) - first).total_seconds() / 86400, 1.0)
            rec.freq = round(rec.hits / elapsed, 4)
        except Exception:
            rec.freq = float(rec.hits)


# ── Lectura con dedupe-at-read (por prediction_id, latest resolved_ts) ──────────

def _dedupe_latest(outcomes: List[Outcome]) -> List[Outcome]:
    latest: Dict[str, Outcome] = {}
    for o in outcomes:
        pid = o.prediction_id or f"{o.subject}"
        prev = latest.get(pid)
        if prev is None or o.resolved_ts >= prev.resolved_ts:
            latest[pid] = o
    return list(latest.values())


def verified_score() -> DomainScore:
    """DomainScore ACUMULADO (deduplicado por prediction_id)."""
    return score_outcomes(DOMAIN, _dedupe_latest(vledger.load_outcomes(DOMAIN)))


def verified_subjects(min_decisive: int = 30) -> Dict[str, DomainScore]:
    """Subjects con criterio VALIDADO (≥min_decisive decisivos), deduplicados."""
    by_subject: Dict[str, List[Outcome]] = {}
    for o in _dedupe_latest(vledger.load_outcomes(DOMAIN)):
        by_subject.setdefault(o.subject, []).append(o)
    out: Dict[str, DomainScore] = {}
    for subj, lst in by_subject.items():
        sc = score_outcomes(DOMAIN, lst)
        if sc.n_decisive >= min_decisive:
            out[subj] = sc
    return out


def resolve_best_subject(dims: Mapping[str, Optional[str]],
                         min_decisive: int = 30) -> Optional[Tuple[str, DomainScore]]:
    """Fallback jerárquico: elige el nivel más específico presente cuyo subject
    tenga ≥min_decisive decisivos y al menos 1 win. Devuelve (subject_key, score)."""
    validated = verified_subjects(min_decisive=min_decisive)
    for _level, subject_key in subject_ladder(dims):
        sc = validated.get(subject_key)
        if sc and sc.wins > 0:
            return subject_key, sc
    return None
