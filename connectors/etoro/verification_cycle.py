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


def _mark_verified(ids: Iterable[str]) -> bool:
    """Marca los `signal_id` ya volcados. Devuelve False si no pudo escribir.

    Antes devolvía None y registraba el fallo en DEBUG. Un marcador que no se
    escribe hace que el ciclo siguiente vuelva a presentar esas señales, así
    que el fallo tiene consecuencias y tiene que verse.
    """
    try:
        current = _load_verified_ids()
        current.update(i for i in ids if i)
        path = _verified_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(current), f)
        return True
    except Exception as exc:
        logger.warning(
            "market.verification | no se pudo escribir el marcador (%s): esas "
            "señales se volverán a presentar; el ledger NO se duplicará", exc,
        )
        return False


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


def _already_in_ledger() -> set:
    """`prediction_id` que el verification_ledger de market YA contiene.

    EL ÚNICO ESLABÓN SIN LLAVE DE IDEMPOTENCIA
    ------------------------------------------
    La gravedad deduplica por `prediction_id` y la procedencia por su clave
    primaria, pero el `verification_ledger` es un JSONL append-only y NO
    deduplica: escribir dos veces el mismo resultado son dos líneas, y el
    DomainScore acumulado cuenta las dos.

    Eso importaba porque el marcador de señales verificadas puede fallar
    DESPUÉS de que el ledger ya esté escrito (disco lleno, permisos). Al ciclo
    siguiente la señal se vuelve a presentar —correcto, el marcador no se
    escribió— y el resultado entraba en el ledger por segunda vez: 1 resultado,
    2 filas, mientras la gravedad conservaba una sola. El desempeño acumulado
    quedaba inflado por un fallo de escritura de un fichero auxiliar.

    No se corrige en el ledger: `connectors/cybersecurity/verification_cycle`
    depende de poder APPENDear una fila que supersede a otra (el flip
    LOSS→WIN) y deduplica en la LECTURA. Cambiar el núcleo rompería ese
    contrato. La comprobación vive aquí, en market, que sí exige una sola
    escritura por señal.

    Se apoya en la caché por mtime de `load_outcomes`, así que en un ciclo sin
    escrituras nuevas no vuelve a leer el fichero.
    """
    try:
        return {
            o.prediction_id for o in vledger.load_outcomes(_DOMAIN)
            if o.prediction_id
        }
    except Exception as exc:
        # Sin la lista no se puede garantizar el "una sola vez". Se devuelve
        # vacío —el comportamiento anterior— y queda constancia.
        logger.warning(
            "market.verification | no se pudo leer el ledger para deduplicar "
            "(%s): una reescritura podría duplicar una fila", exc,
        )
        return set()


def _verify(signals: Iterable[Any], record: bool):
    """Cuerpo de la verificación. Devuelve ``(score, handled_ids)``.

    ``handled_ids`` son los `signal_id` cuyo resultado quedó DURABLEMENTE
    contabilizado: aplicado a la estrella, reconocido como duplicado o
    aparcado para reintento. Solo esos puede marcar el ciclo como verificados.

    ORDEN DE LAS ESCRITURAS
    -----------------------
    Primero la gravedad, después el ledger. Es deliberado: si el almacén de
    `outcome_gravity` está bloqueado por otro escritor, el lote NO queda
    contabilizado, y entonces tampoco se escribe en el ledger ni se marca la
    señal. Así las tres cosas —ledger, gravedad y marcador— avanzan juntas o
    no avanzan, y el siguiente ciclo repite el lote completo sin duplicar
    nada. Al revés (ledger primero) un bloqueo dejaba el ledger escrito y la
    señal marcada con el resultado perdido, o bien lo duplicaba en el ledger
    al reintentar.

    Una señal cuyo resultado es PENDING tampoco se marca: todavía no está
    verificada, y marcarla la habría excluido para siempre.
    """
    outcomes: List[Outcome] = []
    to_gravity: List[tuple] = []
    sig_by_prediction: Dict[str, Any] = {}
    for sig in signals:
        pred, obs = _signal_to_pair(sig)
        outcome = _ADAPTER.resolve(pred, obs)
        outcomes.append(outcome)
        if record and outcome.status is not OutcomeStatus.PENDING:
            to_gravity.append((star_fingerprint_for(outcome.subject), outcome))
            sig_by_prediction[outcome.prediction_id] = outcome

    fed: Dict[str, int] = {}
    handled_ids: List[str] = []
    if to_gravity:
        fed = outcome_gravity.apply_verified_outcomes(
            to_gravity, source=_GRAVITY_SOURCE,
        )
        if not outcome_gravity.accounted(fed, len(to_gravity)):
            logger.warning(
                "market.verification | lote NO contabilizado (%d resultados): "
                "no se marcan como verificados; se repetirán en el próximo ciclo",
                fed.get(outcome_gravity.FAILED, 0),
            )
        else:
            already = _already_in_ledger()
            for prediction_id, outcome in sig_by_prediction.items():
                if prediction_id in already:
                    # Ya está en el ledger de una pasada anterior cuyo marcador
                    # no llegó a escribirse. Volver a escribirlo duplicaría la
                    # fila; darlo por atendido es lo correcto, porque el
                    # resultado SÍ está donde tenía que estar.
                    handled_ids.append(prediction_id)
                    continue
                # `record_outcome` NO lanza: devuelve False si no pudo escribir
                # (disco lleno, permisos). Ignorar ese False marcaba la señal
                # como verificada con el ledger sin su resultado, y la señal no
                # se volvía a presentar. Solo se marca lo que quedó escrito.
                if vledger.record_outcome(outcome):
                    handled_ids.append(prediction_id)
                else:
                    logger.warning(
                        "market.verification | el ledger rechazó %s: no se "
                        "marca como verificada; se repetirá en el próximo ciclo",
                        prediction_id,
                    )

    score = score_outcomes(_DOMAIN, outcomes)
    logger.info(
        "market.verification | batch=%d | decisive=%d | WR=%.0f%% | acc=%.2f "
        "| gravity_applied=%d | contabilizados=%d",
        score.n_total, score.n_decisive, score.win_rate, score.accuracy,
        fed.get(outcome_gravity.APPLIED, 0), len(handled_ids),
    )
    return score, handled_ids


def verify_signals(signals: Iterable[Any], record: bool = True) -> DomainScore:
    """Resuelve las señales dadas en Outcomes verificados vía TradingOutcomeAdapter.

    Reusa el clasificador canónico dentro del adaptador (sin duplicar win/loss).
    Persiste los no-PENDING en el ledger (si ``record``) y lleva los DECISIVOS a
    la estrella del símbolo, que es de donde `qualify_pattern()` lee el
    desempeño real. Devuelve el DomainScore de ESTE lote (el acumulado vive en
    el ledger).
    """
    return _verify(signals, record)[0]


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

    score, handled_ids = _verify(fresh, record=record)
    if record:
        # SOLO las que quedaron contabilizadas. Marcar el lote entero era el
        # agujero: ante un bloqueo de la base o un resultado todavía PENDING,
        # la señal quedaba marcada y no se volvía a presentar nunca, así que
        # su resultado se perdía aunque el problema fuera transitorio.
        if handled_ids and not _mark_verified(handled_ids):
            # El ledger y la gravedad ya están escritos; lo único que falta es
            # el marcador. El próximo ciclo repetirá estas señales y ninguna
            # de las dos se duplicará: la gravedad por su clave, el ledger por
            # `_already_in_ledger()`.
            logger.warning(
                "market.verification | %d señales quedaron sin marcar; se "
                "repetirán sin duplicar nada", len(handled_ids),
            )
    return score


def verified_score() -> DomainScore:
    """DomainScore ACUMULADO (todas las verificaciones market persistidas)."""
    return vledger.domain_score(_DOMAIN)


def verified_subjects(min_decisive: int = 3) -> Dict[str, DomainScore]:
    """Símbolos con criterio VALIDADO (≥min_decisive resultados)."""
    return vledger.subject_scores(_DOMAIN, min_decisive=min_decisive)
