"""
connectors/etoro/ta_feature_gravity.py — Conocimiento técnico (TA-Lib) como
condiciones bucketizadas, y su consulta en el momento de decidir.

Corrección 2026-09-26: este módulo YA NO orquesta nada por su cuenta -- no
tiene contrato propio, no escanea señales, no clasifica procedencia. Esa
etapa (conocimiento TA-Lib ↔ outcome verificado) ahora vive fusionada en
``connectors.etoro.verification_cycle`` (dominio ``market``, mismo pase que
ya verifica cada señal contra el precio): un outcome verificado alimenta a
la vez la estrella del símbolo y las estrellas de sus condiciones TA-Lib
activas, con el MISMO origen -- una señal PAPER resuelta contra precio real
cuenta como evidencia real para las dos, porque la procedencia es la misma
función (``verification_cycle._origin_of``/``_origin_kind``) para ambas.
Antes este módulo clasificaba su propia procedencia por separado
(``live_activated_at``), lo que dejaba casi toda la evidencia marcada
'simulated' aunque viniera del mismo precio real que ya admite `market`.

Lo que queda aquí es la parte PURA y reutilizable:
  - ``bucketize()`` / ``conditions_for_signal()``: features TA-Lib de un
    snapshot -> condiciones discretas. Sin E/S, sin red, sin gravedad.
  - ``star_fingerprint_for()``: identidad de la estrella de una condición.
  - ``ensure_condition_stars()``: crea/toca (``gravity_engine.record_event``)
    la estrella de cada condición activa -- llamado desde
    ``verification_cycle`` en el mismo pase que verifica la señal, no desde
    un scan aparte.
  - ``current_conditions_for_proposal()`` / ``graduated_verdict()``: consulta
    en tiempo de decisión, para ``entry_validator`` (condición 9) -- sin
    cambios de comportamiento por esta corrección; siguen leyendo
    ``verified_outcomes`` vía ``derive_pattern_stats()``, que ahora sí puede
    graduar con evidencia real (heredada de `market`) en vez de quedar
    siempre en `None`.

Este módulo deliberadamente NO:
  - detecta cruces (MACD, medias): compute_knowledge() da solo el último
    valor de cada serie, no la serie completa: un cruce necesitaría dos
    snapshots (dos llamadas reales a la API) por señal. Queda para una
    iteración posterior con su propio diseño.
  - bucketiza ATR: es dependiente del símbolo (5% de ATR/precio es normal en
    BTC, extremo en SPY) y necesitaría una referencia histórica por símbolo
    que hoy no existe en ningún lado. Fuera a propósito de esta versión.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.etoro.ta_feature_gravity")

_DOMAIN = "market_ta_features"

# Mismo vocabulario de timeframes que knowledge_snapshot.DEFAULT_TIMEFRAMES,
# abreviado para que el condition_id sea legible en el fingerprint.
_TF_LABEL = {
    "FifteenMinutes": "15m",
    "OneHour": "1H",
    "FourHours": "4H",
    "OneDay": "1D",
    "OneWeek": "1W",
}


# ---------------------------------------------------------------------------
# Bucketización -- pura, sin E/S, sin red.
# ---------------------------------------------------------------------------

def bucketize(timeframe_features: Dict[str, Any], price: float) -> List[str]:
    """Condiciones discretas observables en UN único snapshot de UN timeframe.

    Recibe el dict `features` de un solo timeframe -- la misma forma que ya
    devuelve `knowledge_snapshot.snapshot_timeframe()['features']` -- no el
    snapshot multi-timeframe completo. El llamador (`conditions_for_signal`)
    itera los timeframes disponibles y llama esto una vez por cada uno,
    añadiendo el sufijo de timeframe después.

    Solo incluye condiciones que no requieren comparar contra el valor
    anterior de la serie (ver docstring del módulo). Nunca lanza: un feature
    ausente, no numérico, o `None` (NaN de TA-Lib) simplemente no produce
    ninguna condición -- no rompe el resto del cálculo.
    """
    conditions: List[str] = []

    def _num(name: str) -> Optional[float]:
        v = timeframe_features.get(name)
        return float(v) if isinstance(v, (int, float)) else None

    rsi = _num("RSI")
    if rsi is not None:
        if rsi < 30:
            conditions.append("RSI:oversold")
        elif rsi > 70:
            conditions.append("RSI:overbought")

    macd_hist = _num("MACD_macdhist")
    if macd_hist is not None:
        conditions.append(
            "MACD:hist_positive" if macd_hist > 0 else "MACD:hist_negative"
        )

    adx = _num("ADX")
    if adx is not None and adx > 25:
        conditions.append("ADX:strong_trend")

    stoch_k = _num("STOCH_slowk")
    if stoch_k is not None:
        if stoch_k < 20:
            conditions.append("STOCH:oversold")
        elif stoch_k > 80:
            conditions.append("STOCH:overbought")

    upper = _num("BBANDS_upperband")
    lower = _num("BBANDS_lowerband")
    if price:
        if upper is not None and price > upper:
            conditions.append("BBANDS:price_above_upper")
        if lower is not None and price < lower:
            conditions.append("BBANDS:price_below_lower")

    # Patrones de vela: TA-Lib ya los devuelve discretos, con signo
    # (100 = alcista, -100 = bajista, 0 = ausente) -- mapeo directo, sin
    # umbral que inventar.
    for key, value in timeframe_features.items():
        if key.startswith("CDL") and isinstance(value, (int, float)) and value != 0:
            conditions.append(f"{key}:bullish" if value > 0 else f"{key}:bearish")

    return conditions


def conditions_for_signal(
    features_by_timeframe: Dict[str, Any], price: float
) -> List[str]:
    """Todas las condiciones de una señal, con su timeframe como sufijo.

    `features_by_timeframe` es la entrada completa de `knowledge_ledger`
    (`entry["features"]`): `{timeframe: {available, features, ...}, ...}`.
    Un timeframe con `available=False` (profundidad insuficiente) no aporta
    condiciones -- no inventa nada sobre datos que no llegaron.
    """
    out: List[str] = []
    for tf_name, tf_data in (features_by_timeframe or {}).items():
        if not isinstance(tf_data, dict) or not tf_data.get("available"):
            continue
        label = _TF_LABEL.get(tf_name, tf_name)
        for cond in bucketize(tf_data.get("features", {}) or {}, price):
            out.append(f"{cond}:{label}")
    return out


# ---------------------------------------------------------------------------
# Identidad / fingerprint -- reutilizando lo que ya existe.
# ---------------------------------------------------------------------------

def star_fingerprint_for(symbol: str, condition_id: str) -> str:
    """Mismo espacio textual que `verification_cycle.star_fingerprint_for`
    (`market:{symbol}`), con un sub-namespace `:ta:` para no colisionar
    nunca con la estrella de símbolo que ya existe."""
    return f"market:{str(symbol or '').upper()}:ta:{condition_id}"


def ensure_condition_stars(symbol: str, conditions: List[str]) -> None:
    """Crea (o toca -- refresca `hits`/`last_seen`) la estrella de cada
    condición activa. MISMO `gravity_engine.record_event()` que ya usa
    cualquier otro dominio para nacer una estrella nueva; llamado desde
    `verification_cycle._ta_condition_outcomes()`, en el mismo pase que ya
    verifica la señal contra el precio -- no un scan aparte.

    Necesario porque `record_verified_outcome()` (lo que anota el resultado
    en la estrella) nunca CREA una estrella que no existe -- la aparca
    (`outcome_gravity.apply_verified_outcomes`, "deferred"). Esta llamada es
    lo que garantiza que ya exista antes de que el commit() de
    verification_cycle intente anotarle el resultado."""
    from core.learn.gravity_engine import get_gravity_index

    gi = get_gravity_index()
    for condition_id in conditions:
        try:
            gi.record_event(
                fingerprint=star_fingerprint_for(symbol, condition_id),
                domain=_DOMAIN,
                intent=f"{symbol}:{condition_id}",
                outcome="observed",
                summary=f"Condición TA-Lib observada: {condition_id} ({symbol})",
            )
        except Exception as exc:
            logger.debug(
                "ensure_condition_stars failed for %s/%s: %s",
                symbol, condition_id, exc,
            )


# ---------------------------------------------------------------------------
# Consulta en tiempo de decisión -- para entry_validator.
# ---------------------------------------------------------------------------

#: Un único timeframe, no los 5 -- este chequeo corre en el camino de
#: decisión de CADA propuesta evaluada, así que se mantiene barato a
#: propósito (1 llamada real a la API, no 5).
_ENTRY_CHECK_TIMEFRAME = "OneHour"


def current_conditions_for_proposal(symbol: str, price: float) -> List[str]:
    """Condiciones TA-Lib AHORA MISMO para `symbol`, en `_ENTRY_CHECK_TIMEFRAME`.

    Nunca lanza: sin datos disponibles (símbolo no resuelto, profundidad
    insuficiente, fallo de red), lista vacía -- que `graduated_verdict()`
    interpreta como "sin opinión", nunca como bloqueo.
    """
    try:
        from connectors.etoro.knowledge_snapshot import snapshot_at
        snap = snapshot_at(symbol, time.time(), timeframes=[_ENTRY_CHECK_TIMEFRAME])
        tf_data = snap.get(_ENTRY_CHECK_TIMEFRAME) or {}
        if not tf_data.get("available"):
            return []
        label = _TF_LABEL.get(_ENTRY_CHECK_TIMEFRAME, _ENTRY_CHECK_TIMEFRAME)
        return [f"{c}:{label}" for c in bucketize(tf_data.get("features", {}) or {}, price)]
    except Exception as exc:
        logger.debug("current_conditions_for_proposal failed for %s: %s", symbol, exc)
        return []


def graduated_verdict(symbol: str, conditions: List[str]) -> Optional[Dict[str, Any]]:
    """El peor veredicto YA GRADUADO entre las condiciones activas ahora
    mismo para `symbol` -- o `None` si ninguna tiene evidencia REAL
    suficiente todavía.

    "Graduado" usa los MISMOS umbrales que el resto del sistema
    (`core.domain_knowledge.MIN_SAMPLE/MIN_WIN_RATE/MIN_EXPECTANCY`), sin
    cambios por la corrección 2026-09-26 -- lo único que cambió es que ahora
    SÍ puede llegar a haber evidencia con `origin_kind='real'` que gradúe,
    porque `verification_cycle` ya no le pone a esta evidencia una
    procedencia distinta a la que ya usa para el símbolo.

    El silencio (`None`) es del PROPIO diseño, no un valor por defecto
    olvidado: mientras `derive_pattern_stats()` siga devolviendo `None`
    (evidencia insuficiente o toda `simulated` -- ver
    `core.gravity_kernel.signals._gradable_history` y
    `core.learn.schemas.ADMISSIBLE_ORIGIN_KINDS`), esta función tampoco
    tiene nada que decir. Ausencia de evidencia real NUNCA bloquea una
    entrada; solo evidencia real MOSTRANDO mal desempeño lo hace.
    """
    if not conditions:
        return None
    try:
        from core.domain_knowledge import MIN_SAMPLE, MIN_WIN_RATE, MIN_EXPECTANCY
        from core.learn.gravity_engine import get_gravity_index
        from core.gravity_kernel.signals import derive_pattern_stats
    except Exception as exc:
        logger.debug("graduated_verdict imports failed: %s", exc)
        return None

    gi = get_gravity_index()
    worst: Optional[Dict[str, Any]] = None
    for condition_id in conditions:
        try:
            fp = star_fingerprint_for(symbol, condition_id)
            rec = gi.get(fp)
            if rec is None:
                continue
            stats = derive_pattern_stats(rec)
            if stats is None:
                continue  # sin evidencia admisible todavía -- sin opinión
            if stats["sample_size"] < MIN_SAMPLE:
                continue  # evidencia real, pero insuficiente -- sin opinión
            win_rate_pct = stats["win_rate"] * 100.0
            favorable = win_rate_pct >= MIN_WIN_RATE and stats["expectancy"] > MIN_EXPECTANCY
            if favorable:
                continue  # graduó, y con buen desempeño -- no bloquea nada
            verdict = {
                "condition_id": condition_id,
                "fingerprint": fp,
                "win_rate_pct": round(win_rate_pct, 1),
                "sample_size": int(stats["sample_size"]),
                "real_evidence": int(stats["real"]),
            }
            if worst is None or verdict["win_rate_pct"] < worst["win_rate_pct"]:
                worst = verdict
        except Exception as exc:
            logger.debug("graduated_verdict failed for %s/%s: %s", symbol, condition_id, exc)
    return worst
