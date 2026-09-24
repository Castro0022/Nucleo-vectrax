"""
Vectrax Market — Conocimiento Técnico Formal (TA-Lib)
========================================================
CONOCIMIENTO, no experiencia. Esta función no sabe qué símbolo es, en qué
timestamp está, ni qué hizo Vectrax con esa información — es una lectura
pura y determinista de una serie de velas: la misma estructura que TA-Lib
documenta desde hace décadas (tendencia, momentum, volatilidad, volumen,
patrones de vela), traducida a un solo dict plano.

No decide BUY/SELL. No pondera nada. No sabe qué significó para ningún
resultado. Eso es tarea de la capa de descubrimiento por evidencia (etapa
2), que compara estas lecturas contra outcomes ya verificados.

Regla fijada para Vectrax: el conocimiento NO se preselecciona por
utilidad humana. Si una función de TA-Lib es computable sobre precio real,
se incorpora — sea o no que alguien le vea, de antemano, una
interpretación de manual. Que la evidencia y los outcomes decidan después
su relevancia práctica es tarea de la etapa 2, no de este módulo.

Catálogo cubierto: TODAS las funciones de TA-Lib (196), excepto las 5 que
son estructuralmente NO COMPUTABLES sobre el precio de un activo real,
verificado numéricamente, no por criterio de relevancia:
  - ACOS, ASIN: dominio matemático [-1,1]; el precio de cualquier activo
    real cae fuera de ese rango → devuelven NaN SIEMPRE, para cualquier
    serie de precios real, no solo "raramente".
  - EXP, COSH, SINH: overflow de float64 con precios de la magnitud
    habitual de un activo (p.ej. BTC ~60000) → devuelven `inf`.
Un NaN/inf estructural no es un dato de baja relevancia que la etapa 2
pueda evaluar — es la ausencia de un valor utilizable, para cualquier
input real, no solo para algunos. Esa es la única razón admitida para
excluir algo de este catálogo: no computa, nunca, sobre precio real.
Todo lo demás — incluidas las funciones de "Math Operators"/"Math
Transform" que sí sí devuelven un valor real (MAX/MIN/MAXINDEX/MININDEX/
SUM/CUMSUM de precio, LN/LOG10/SQRT, ADD/SUB/MULT/DIV de high/low,
CEIL/FLOOR, y las trigonométricas ATAN/COS/SIN/TAN/TANH, que son finitas
aunque no tengan literatura de análisis técnico detrás) — se calcula
igual que velas, tendencia, momentum, volatilidad, volumen, estadística,
transformación de precio y ciclos: 196 funciones en total.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence

import numpy as np

logger = logging.getLogger("vectrax.market.ta_knowledge")

try:
    import talib
    from talib import abstract as talib_abstract
    TALIB_AVAILABLE = True
except ImportError:  # pragma: no cover - entorno sin TA-Lib instalado
    talib = None
    talib_abstract = None
    TALIB_AVAILABLE = False
    logger.warning("TA-Lib no está instalado — compute_knowledge devolverá {}")


# Únicas 5 funciones EXCLUIDAS del catálogo (ver docstring del módulo):
# no computan sobre precio real, no que se les juzgue poco relevantes.
# ACOS/ASIN → NaN siempre (dominio [-1,1]); EXP/COSH/SINH → inf siempre
# (overflow con precios de magnitud real). Verificado numéricamente, no
# supuesto — ver tests/test_market_knowledge.py.
_NON_COMPUTABLE_ON_REAL_PRICE = frozenset({"ACOS", "ASIN", "EXP", "COSH", "SINH"})

# Mínimo de barras para intentar el cálculo. Por debajo de esto casi
# ninguna función de TA-Lib puede producir un valor real (además, TA-Lib
# lanza para algunas funciones con arrays demasiado cortos en vez de
# devolver NaN) — se corta antes en vez de dejar que cada función falle.
MIN_BARS = 2


def _function_names() -> list:
    """Nombres de las funciones incluidas: el catálogo COMPLETO de TA-Lib
    menos las 5 no computables sobre precio real. Se recalcula en cada
    llamada porque TA-Lib expone la lista con un costo despreciable — no
    vale la pena el riesgo de una constante de import-time desincronizada."""
    if not TALIB_AVAILABLE:
        return []
    names = []
    for fns in talib.get_function_groups().values():
        names.extend(fns)
    return [n for n in names if n not in _NON_COMPUTABLE_ON_REAL_PRICE]


def known_function_names() -> list:
    """Catálogo completo (196 funciones: las 201 de TA-Lib menos las 5 no
    computables sobre precio real) que `compute_knowledge` intenta
    calcular. Público para que tests / auditorías puedan verificar
    cobertura sin duplicar la lista de exclusión en otro sitio."""
    return sorted(_function_names())


def _last_valid(arr: np.ndarray) -> Optional[float]:
    """Último valor de una serie de salida de TA-Lib, o None si es NaN.
    Nunca lanza; una serie vacía o inválida también da None."""
    try:
        if arr is None or len(arr) == 0:
            return None
        val = arr[-1]
        if val is None:
            return None
        fval = float(val)
        if fval != fval:  # NaN
            return None
        return fval
    except Exception:
        return None


def _round(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(value, 6)


def compute_knowledge(
    open_: Sequence[float],
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    volume: Sequence[float],
) -> Dict[str, Any]:
    """Lectura del catálogo completo de TA-Lib sobre UNA serie de velas.

    Precondición del llamador: los arrays están en orden cronológico
    ASCENDENTE (más antigua primero), y la última posición es la última
    vela CERRADA disponible — esta función no reordena ni valida fugas de
    información; eso es responsabilidad de quien construye la serie
    (`knowledge_snapshot.snapshot_at`, que aplica el corte por timestamp
    antes de llamar aquí).

    Devuelve un dict plano {nombre_funcion: valor} — o
    {nombre_funcion}_{salida} para funciones con múltiples salidas (p.ej.
    MACD → MACD_macd/MACD_macdsignal/MACD_macdhist). Solo el valor MÁS
    RECIENTE de cada serie (la lectura "de este instante"), no la serie
    completa. Los patrones de vela conservan su valor entero con signo
    (-200..200) tal como los devuelve TA-Lib — no se convierten a booleano.
    Un valor ausente (histórico insuficiente para ese indicador) es
    `None`, nunca un número inventado.
    """
    if not TALIB_AVAILABLE:
        return {}

    n = len(close)
    if n < MIN_BARS:
        return {}

    inputs = {
        "open": np.asarray(open_, dtype=np.float64),
        "high": np.asarray(high, dtype=np.float64),
        "low": np.asarray(low, dtype=np.float64),
        "close": np.asarray(close, dtype=np.float64),
        "volume": np.asarray(volume, dtype=np.float64),
    }

    result: Dict[str, Any] = {}
    for name in _function_names():
        try:
            func = talib_abstract.Function(name)
            out = func(inputs)
        except Exception as exc:
            # Función que requiere un parámetro no-OHLCV (p.ej. MAVP con
            # 'periods'), o que no acepta esta combinación de inputs.
            # Se omite esa función puntual — no se inventa un valor y no
            # se aborta el resto del catálogo por una función incompatible.
            logger.debug("ta_knowledge: %s no calculable: %s", name, exc)
            continue

        output_names = func.output_names
        if isinstance(out, (list, tuple)) and len(output_names) > 1:
            for oname, series in zip(output_names, out):
                result[f"{name}_{oname}"] = _round(_last_valid(np.asarray(series)))
        else:
            series = out[0] if isinstance(out, (list, tuple)) else out
            result[name] = _round(_last_valid(np.asarray(series)))

    return result
