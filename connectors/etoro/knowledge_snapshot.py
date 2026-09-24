"""
connectors/etoro/knowledge_snapshot.py — Instantánea de conocimiento en un instante.

Aplica el conocimiento técnico formal (`connectors.market.ta_knowledge`)
sobre velas REALES de eToro, en un timestamp concreto, respetando UNA sola
regla en todo el sistema: para el instante `as_of_ts`, solo se usan velas
cuyo cierre ya ocurrió (`close_time <= as_of_ts`). Nunca la vela en
formación, nunca una vela futura. Esa regla es idéntica en vivo (as_of_ts =
ahora) y en retrospectiva (as_of_ts = un instante pasado, en el backfill de
`knowledge_backfill.py`) — es la MISMA función en los dos casos, no dos
implementaciones distintas del corte de fuga.

Limitación honesta: el endpoint de velas de eToro que envuelve
`etoro_client.get_candles` solo entrega las últimas `count` velas contadas
desde AHORA (no admite rango de fechas). Para reconstruir un instante
lejano en el pasado se pide un `count` generoso y se filtra localmente; si
ni así se alcanza ese instante, la función lo declara explícitamente
(`insufficient_depth=True`) en vez de inventar o aproximar nada.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

from connectors.market.ta_knowledge import compute_knowledge

logger = logging.getLogger("vectrax.etoro.knowledge_snapshot")

# Nombres canónicos de eToro (los mismos que ya usa
# `etoro_client._INTERVAL_MAP` / `market_observer.evaluate`) — se reutiliza
# ese vocabulario en vez de inventar uno nuevo (15m/1h/4h/1d/1w).
DEFAULT_TIMEFRAMES: List[str] = [
    "FifteenMinutes", "OneHour", "FourHours", "OneDay", "OneWeek",
]

DEFAULT_COUNT = 500  # velas a pedir por timeframe antes de filtrar por fecha
MIN_BARS_FOR_DEPTH = 50  # por debajo de esto, se marca depth insuficiente


def _to_epoch(raw: Any) -> Optional[float]:
    """Normaliza el campo `time` de una vela de eToro (epoch o ISO) a epoch
    UTC float. Nunca lanza; None si no se puede interpretar."""
    if raw is None or raw == "":
        return None
    try:
        if isinstance(raw, (int, float)):
            val = float(raw)
            # eToro/Binance suelen dar epoch en milisegundos para timestamps
            # grandes; > 10^12 es inequívocamente ms, no segundos.
            return val / 1000.0 if val > 1e12 else val
        s = str(raw).strip().replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _closed_candles_as_of(candles: List[Dict[str, Any]], as_of_ts: float) -> List[Dict[str, Any]]:
    """Velas con cierre <= as_of_ts, ordenadas ascendente (más antigua
    primero). Descarta cualquier vela sin timestamp interpretable —
    prudente: preferir menos datos a datos con fecha incierta."""
    dated = []
    for c in candles:
        ep = _to_epoch(c.get("time"))
        if ep is None or ep > as_of_ts:
            continue
        dated.append((ep, c))
    dated.sort(key=lambda pair: pair[0])
    return [c for _, c in dated]


def snapshot_timeframe(
    instrument_id: int,
    interval: str,
    as_of_ts: float,
    count: int = DEFAULT_COUNT,
) -> Dict[str, Any]:
    """Instantánea de conocimiento para UN timeframe, en `as_of_ts`.

    Devuelve:
      available          — False si no hay velas usables (ver reason)
      insufficient_depth  — True si hay velas pero pocas para indicadores
                             con lookback largo (no invalida lo calculado,
                             solo advierte que puede haber más `None`)
      bars_used            — cuántas velas cerradas <= as_of_ts se usaron
      as_of_candle_time    — epoch de la última vela usada (la más cercana
                              a as_of_ts sin pasarla)
      features              — dict de connectors.market.ta_knowledge
    """
    from connectors.etoro.etoro_client import get_candles

    raw = get_candles(instrument_id, interval=interval, count=count, direction="desc")
    if not raw.get("success"):
        return {
            "available": False,
            "reason": f"get_candles falló: {raw.get('error', 'desconocido')}",
            "insufficient_depth": True,
            "bars_used": 0,
            "as_of_candle_time": None,
            "features": {},
        }

    closed = _closed_candles_as_of(raw.get("candles", []), as_of_ts)
    if len(closed) < 2:
        return {
            "available": False,
            "reason": (
                "sin velas cerradas <= as_of_ts dentro de la ventana "
                f"disponible (count={count}) — el instante pedido cae "
                "fuera de la profundidad histórica que este endpoint "
                "puede entregar"
            ),
            "insufficient_depth": True,
            "bars_used": len(closed),
            "as_of_candle_time": None,
            "features": {},
        }

    features = compute_knowledge(
        open_=[c["open"] for c in closed],
        high=[c["high"] for c in closed],
        low=[c["low"] for c in closed],
        close=[c["close"] for c in closed],
        volume=[c["volume"] for c in closed],
    )

    return {
        "available": True,
        "reason": "",
        "insufficient_depth": len(closed) < MIN_BARS_FOR_DEPTH,
        "bars_used": len(closed),
        "as_of_candle_time": _to_epoch(closed[-1].get("time")),
        "features": features,
    }


def snapshot_at(
    symbol: str,
    as_of_ts: float,
    timeframes: Optional[List[str]] = None,
    count: int = DEFAULT_COUNT,
) -> Dict[str, Any]:
    """Conocimiento técnico completo de `symbol` en el instante `as_of_ts`,
    para cada timeframe en `timeframes` (por defecto 15m/1H/4H/1D/1W).

    Estructura de salida: { timeframe: snapshot_timeframe(...), ... }.
    Nunca lanza; un símbolo no resoluble devuelve cada timeframe marcado
    `available=False` con el motivo, no un dict vacío silencioso.
    """
    from connectors.etoro.etoro_client import get_instrument_id

    tfs = timeframes or DEFAULT_TIMEFRAMES
    iid = get_instrument_id(symbol)
    if iid is None:
        reason = f"instrumento '{symbol}' no encontrado en eToro"
        return {
            tf: {
                "available": False, "reason": reason, "insufficient_depth": True,
                "bars_used": 0, "as_of_candle_time": None, "features": {},
            }
            for tf in tfs
        }

    out: Dict[str, Any] = {}
    for tf in tfs:
        try:
            out[tf] = snapshot_timeframe(iid, tf, as_of_ts, count=count)
        except Exception as exc:
            logger.debug("snapshot_at(%s, %s) falló: %s", symbol, tf, exc)
            out[tf] = {
                "available": False, "reason": f"error inesperado: {exc}",
                "insufficient_depth": True, "bars_used": 0,
                "as_of_candle_time": None, "features": {},
            }
    return out
