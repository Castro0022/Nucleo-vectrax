"""
connectors/etoro/verification_cycle.py — Cierre del ciclo VERIFICADO de market.

Convierte las señales de mercado RESUELTAS (con precio realizado) en Outcomes
verificados vía ``TradingOutcomeAdapter`` —que envuelve el clasificador CANÓNICO
``outcome_tracker.classify_outcome`` (única fuente de verdad win/loss)— los
persiste en el ``verification_ledger`` genérico y devuelve el ``DomainScore``
REAL (WR/accuracy contra la verdad del precio, por símbolo).

Mismo patrón que ``connectors/freight/verification_cycle.py``. Aditivo: NO toca
señales / patrones / proposals / ejecución. Deduplica por ``signal_id`` (cada
señal se verifica UNA sola vez) para no doble-contar en el ledger acumulado.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Iterable, List

from core.learn.outcome_adapter import (
    DomainScore,
    Outcome,
    OutcomeStatus,
    Prediction,
    score_outcomes,
)
from core.learn import outcome_gravity
from core.learn import verification_ledger as vledger
from connectors.etoro.trading_outcome_adapter import TradingOutcomeAdapter

logger = logging.getLogger("vectrax.etoro.verification_cycle")

_DOMAIN = "market"
_ADAPTER = TradingOutcomeAdapter()

#: Quién aplicó el resultado, guardado en cada fila de procedencia.
_GRAVITY_SOURCE = "etoro.verification_cycle"


# ── Estado de deduplicación (señales ya verificadas) ───────────────────
# Un JSON con los signal_id ya volcados al ledger, en el mismo vault que el
# verification_ledger (path resuelto en runtime vía VECTRAX_VAULT_DIR).

def _vault_dir() -> str:
    return os.environ.get(
        "VECTRAX_VAULT_DIR",
        os.path.join(os.path.expanduser("~"), "Vectrax", "vault"),
    )


def _verified_path() -> str:
    return os.path.join(_vault_dir(), "domain_verification", "market_verified.json")


def _load_verified_ids() -> set:
    try:
        path = _verified_path()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return set(json.load(f))
    except Exception as exc:
        logger.debug("market verified-ids load failed: %s", exc)
    return set()


def _mark_verified(ids: Iterable[str]) -> None:
    try:
        current = _load_verified_ids()
        current.update(i for i in ids if i)
        path = _verified_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(current), f)
    except Exception as exc:
        logger.debug("market verified-ids write failed: %s", exc)


# ── Mapeo señal → (Prediction, observation) ────────────────────────────

def _signal_to_pair(sig: Any):
    """(Prediction, observation) desde una MarketSignal resuelta.

    subject = símbolo; entry = entry_price (o price si falta); la verdad es el
    ``outcome_price`` realizado. window_expired se deriva del status registrado.
    """
    symbol = str(getattr(sig, "symbol", "") or "").upper()
    direction = str(getattr(sig, "direction", "") or "buy").lower()
    entry = getattr(sig, "entry_price", None) or getattr(sig, "price", 0.0) or 0.0
    pred = Prediction(
        domain=_DOMAIN,
        subject=symbol or "unknown",
        predicted=direction,
        # Identidad REAL de la predicción. Antes quedaba en "" (el default),
        # lo que dejaba cada Outcome sin forma de distinguirse de otro: ni el
        # ledger ni la deduplicación de `outcome_gravity` podían decir si dos
        # resultados eran el mismo verificado dos veces o dos señales
        # distintas del mismo símbolo. `signal_id` ya es único por señal.
        prediction_id=str(getattr(sig, "signal_id", "") or ""),
        context={
            "direction": direction,
            "entry_price": entry,
            "invalidation_price": getattr(sig, "invalidation_price", None),
        },
    )
    obs = {
        "current_price": getattr(sig, "outcome_price", None),
        "window_expired": str(getattr(sig, "status", "")).lower() == "expired",
    }
    return pred, obs


def star_fingerprint_for(symbol: str) -> str:
    """Identidad de la estrella gravitacional del símbolo.

    Es LITERALMENTE la convención que usa
    `connectors/etoro/learning_engine._feed_gravity` al crear la estrella
    (``f"market:{sym}"`` con el símbolo en mayúsculas), y el mismo espacio de
    identidad que `star_a`/`star_b` de una convergencia. Aquí no hay
    traducción posible ni necesaria: el `subject` de la verificación YA es el
    símbolo. Existe como función, y no en línea, para que la prueba del
    recorrido completo pueda afirmar la paridad entre quien crea la estrella y
    quien le anota resultados.
    """
    return f"market:{str(symbol or '').upper()}"


def verify_signals(signals: Iterable[Any], record: bool = True) -> DomainScore:
    """Resuelve las señales dadas en Outcomes verificados vía TradingOutcomeAdapter.

    Reusa el clasificador canónico dentro del adaptador (sin duplicar win/loss).
    Persiste los no-PENDING en el ledger (si ``record``) y lleva los DECISIVOS a
    la estrella del símbolo, que es de donde `qualify_pattern()` lee el
    desempeño real. Devuelve el DomainScore de ESTE lote (el acumulado vive en
    el ledger).
    """
    outcomes: List[Outcome] = []
    to_gravity: List[tuple] = []
    for sig in signals:
        pred, obs = _signal_to_pair(sig)
        outcome = _ADAPTER.resolve(pred, obs)
        outcomes.append(outcome)
        if record and outcome.status is not OutcomeStatus.PENDING:
            vledger.record_outcome(outcome)
            to_gravity.append((star_fingerprint_for(outcome.subject), outcome))
    fed = outcome_gravity.apply_verified_outcomes(
        to_gravity, source=_GRAVITY_SOURCE,
    ) if to_gravity else {}
    score = score_outcomes(_DOMAIN, outcomes)
    logger.info(
        "market.verification | batch=%d | decisive=%d | WR=%.0f%% | acc=%.2f "
        "| gravity_applied=%d",
        score.n_total, score.n_decisive, score.win_rate, score.accuracy,
        fed.get(outcome_gravity.APPLIED, 0),
    )
    return score


def run_market_verification(record: bool = True) -> DomainScore:
    """Entrada del ciclo: verifica las señales RESUELTAS aún no verificadas.

    Carga las señales con status != pending y precio realizado, filtra las ya
    verificadas (dedup por signal_id) y las resuelve vía ``verify_signals``.
    Marca las procesadas como verificadas para no doble-contar en el acumulado.
    Defensivo: nunca lanza (devuelve un DomainScore vacío ante cualquier error).

    ANTES DE NADA se reintentan los resultados aparcados. Es obligatorio que
    ocurra aquí y no dentro de ``verify_signals``: el marcador de `signal_id`
    de abajo hace que una señal ya verificada NO se vuelva a presentar nunca.
    Si el reintento dependiera de que esa señal reapareciera en un lote, un
    resultado verificado antes de que existiera su estrella se perdería para
    siempre aunque la estrella apareciera después. También tiene que estar por
    delante de todas las salidas tempranas: un ciclo sin señales nuevas sigue
    teniendo que recuperar lo aparcado.
    """
    outcome_gravity.retry_pending(_DOMAIN)

    try:
        from connectors.etoro.signal_recorder import load_signals, SignalStatus
        pending_value = SignalStatus.PENDING.value
    except Exception as exc:
        logger.debug("market verification: load_signals unavailable: %s", exc)
        return DomainScore(domain=_DOMAIN)

    try:
        resolved = [
            s for s in load_signals()
            if str(getattr(s, "status", "")) != pending_value
            and getattr(s, "outcome_price", None) is not None
        ]
    except Exception as exc:
        logger.debug("market verification: load_signals failed: %s", exc)
        return DomainScore(domain=_DOMAIN)

    if not resolved:
        return DomainScore(domain=_DOMAIN)

    verified = _load_verified_ids()
    fresh = [s for s in resolved if getattr(s, "signal_id", None) not in verified]
    if not fresh:
        return DomainScore(domain=_DOMAIN)

    score = verify_signals(fresh, record=record)
    if record:
        _mark_verified([getattr(s, "signal_id", "") for s in fresh])
    return score


def verified_score() -> DomainScore:
    """DomainScore ACUMULADO (todas las verificaciones market persistidas)."""
    return vledger.domain_score(_DOMAIN)


def verified_subjects(min_decisive: int = 3) -> Dict[str, DomainScore]:
    """Símbolos con criterio VALIDADO (≥min_decisive resultados)."""
    return vledger.subject_scores(_DOMAIN, min_decisive=min_decisive)
