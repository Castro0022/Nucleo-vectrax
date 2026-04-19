"""
Vectrax Intention Projector — Ponderación de Memoria e Intención Futura
=========================================================================
Detecta hacia dónde va el usuario antes de que lo diga.

Principio:
  Si el usuario lleva N conversaciones hablando del mismo tema,
  Vectrax proyecta una intención activa y la usa como contexto
  sin que el usuario tenga que repetirlo.

Mecanismo:
  1. Lee core_memory del usuario (pesos + categorías)
  2. Analiza últimas N interacciones buscando temas recurrentes
  3. Pondera por: frecuencia + peso de memoria + recencia
  4. Proyecta una intención dominante si supera el umbral
  5. Devuelve hipótesis activa para inyectar en el prompt

Privacidad: solo opera sobre metadata y conteos — nunca sobre texto raw.

Creado: 2026-04-02
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.intention_projector")

_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "user_memory.db",
)

# Ventana de análisis
RECENT_INTERACTIONS = 40   # últimas N interacciones a analizar
MIN_FREQUENCY = 3          # tema debe aparecer al menos N veces
DOMINANCE_RATIO = 0.25     # tema debe representar >25% de las menciones
WEIGHT_THRESHOLD = 0.6     # peso mínimo en core_memory para ser "activo"
CACHE_TTL = 900            # caché de 15 min por usuario

_cache: Dict[str, Tuple[float, str]] = {}  # user_id → (ts, intention)


# ---------------------------------------------------------------------------
# Vocabulario de temas
# ---------------------------------------------------------------------------

_TOPIC_PATTERNS: List[Tuple[str, str, List[str]]] = [
    # (topic_id, label_es, keywords)
    ("launch",      "lanzamiento comercial",
     ["lanzar", "launch", "vender", "clientes", "precio", "plan", "cobrar",
      "stripe", "pagar", "monetizar", "comercializar", "usuarios"]),
    ("build",       "construcción del sistema",
     ["construir", "building", "implementar", "código", "módulo", "deploy",
      "servidor", "gateway", "pipeline", "api", "función", "arquitectura"]),
    ("team",        "formación de equipo",
     ["equipo", "team", "socio", "colaborador", "contratar", "miembro",
      "join", "invitar", "personas"]),
    ("market",      "análisis de mercado",
     ["mercado", "market", "btc", "bitcoin", "crypto", "precio", "trading",
      "señal", "análisis técnico", "tendencia"]),
    ("identity",    "definición de identidad",
     ["quién soy", "me llamo", "soy", "nombre", "identidad", "perfil"]),
    ("growth",      "crecimiento y escala",
     ["escalar", "crecer", "1000 usuarios", "200 usuarios", "escala",
      "crecimiento", "tráfico", "rendimiento"]),
    ("product",     "definición de producto",
     ["producto", "feature", "funcionalidad", "experiencia", "usuario",
      "interfaz", "ux", "flujo", "onboarding"]),
    ("learning",    "aprendizaje del sistema",
     ["aprender", "mejorar", "ciclo", "auditor", "memoria", "contexto",
      "intención", "patrón", "propuesta"]),
]

_TOPIC_BY_KEYWORD: Dict[str, str] = {}
for tid, label, kws in _TOPIC_PATTERNS:
    for kw in kws:
        _TOPIC_BY_KEYWORD[kw.lower()] = tid

_TOPIC_LABELS: Dict[str, str] = {tid: label for tid, label, _ in _TOPIC_PATTERNS}


# ---------------------------------------------------------------------------
# Análisis de texto
# ---------------------------------------------------------------------------

def _count_topics(texts: List[str]) -> Counter:
    """Cuenta qué temas aparecen en los textos dados."""
    counts: Counter = Counter()
    for text in texts:
        t = text.lower()
        seen = set()
        for kw, tid in _TOPIC_BY_KEYWORD.items():
            if kw in t and tid not in seen:
                counts[tid] += 1
                seen.add(tid)
    return counts


def _weight_topics(
    counts: Counter,
    core_weights: Dict[str, float],
    recency_bonus: float = 1.2,
) -> Dict[str, float]:
    """
    Pondera los temas por:
      - frecuencia en interacciones recientes
      - peso en core_memory (si hay entradas relacionadas)
      - bonus de recencia (últimas interacciones pesan más)
    """
    weighted: Dict[str, float] = {}
    for tid, count in counts.items():
        base = float(count)
        # Bonus si hay core_memory relacionada
        core_bonus = core_weights.get(tid, 0.0)
        weighted[tid] = base * (1.0 + core_bonus) * recency_bonus
    return weighted


# ---------------------------------------------------------------------------
# Core memory weights por tema
# ---------------------------------------------------------------------------

def _load_core_weights(user_id: str) -> Dict[str, float]:
    """Lee core_memory y mapea sus categorías a topic_ids con sus pesos."""
    weights: Dict[str, float] = {}
    try:
        conn = sqlite3.connect(_DB, timeout=2)
        rows = conn.execute(
            "SELECT category, content, weight FROM core_memory WHERE user_id=?",
            (user_id,),
        ).fetchall()
        conn.close()

        for cat, content, w in rows:
            if w < WEIGHT_THRESHOLD:
                continue
            c = content.lower()
            # Mapear categoría y contenido a topic_id
            for kw, tid in _TOPIC_BY_KEYWORD.items():
                if kw in c:
                    if tid not in weights or weights[tid] < w:
                        weights[tid] = float(w)
    except Exception:
        pass
    return weights


# ---------------------------------------------------------------------------
# Proyección principal
# ---------------------------------------------------------------------------

@dataclass
class IntentionProjection:
    topic_id: str
    label: str
    score: float
    confidence: str   # "high" | "medium" | "low"
    hypothesis: str   # texto para inyectar en el prompt


def project_user_intention(user_id: str) -> Optional[IntentionProjection]:
    """
    Proyecta la intención activa del usuario basada en memoria ponderada.

    Retorna IntentionProjection o None si no hay patrón claro.
    """
    # Cache
    if user_id in _cache:
        ts, cached = _cache[user_id]
        if time.time() - ts < CACHE_TTL and cached:
            # Reconstruir desde cache string
            for tid, label, _ in _TOPIC_PATTERNS:
                if label in cached:
                    return IntentionProjection(
                        topic_id=tid, label=label, score=0.0,
                        confidence="medium", hypothesis=cached,
                    )

    try:
        conn = sqlite3.connect(_DB, timeout=2)
        rows = conn.execute(
            "SELECT user_input FROM interactions WHERE user_id=? "
            "ORDER BY timestamp DESC LIMIT ?",
            (user_id, RECENT_INTERACTIONS),
        ).fetchall()
        conn.close()
    except Exception:
        return None

    if len(rows) < 5:  # no hay suficiente historial
        return None

    texts = [r[0] for r in rows if r[0]]
    topic_counts = _count_topics(texts)

    if not topic_counts:
        return None

    # Cargar pesos de core_memory
    core_weights = _load_core_weights(user_id)

    # Ponderar
    weighted = _weight_topics(topic_counts, core_weights)
    if not weighted:
        return None

    # Detectar tema(s) dominantes — manejar colisiones
    total_score = sum(weighted.values())
    sorted_topics = sorted(weighted.items(), key=lambda x: x[1], reverse=True)

    if not sorted_topics:
        return None

    top_tid, top_score = sorted_topics[0]
    top_dominance = top_score / max(total_score, 1)
    raw_count = topic_counts.get(top_tid, 0)

    if raw_count < MIN_FREQUENCY or top_dominance < DOMINANCE_RATIO:
        return None

    # Detectar colisión: segundo tema dentro del 5% del primero
    collision_topic = None
    if len(sorted_topics) > 1:
        sec_tid, sec_score = sorted_topics[1]
        sec_dominance = sec_score / max(total_score, 1)
        sec_count = topic_counts.get(sec_tid, 0)
        gap = top_dominance - sec_dominance
        if gap < 0.05 and sec_count >= MIN_FREQUENCY:
            collision_topic = (sec_tid, sec_dominance, sec_count)

    label = _TOPIC_LABELS.get(top_tid, top_tid)
    confidence = "high" if top_dominance > 0.4 else "medium" if top_dominance > 0.28 else "low"

    if collision_topic:
        # Proyección dual — dos intenciones concurrentes
        sec_tid2, sec_dom, sec_cnt = collision_topic
        sec_label = _TOPIC_LABELS.get(sec_tid2, sec_tid2)
        hypothesis = (
            f"[Intenciones concurrentes] El usuario navega entre dos focos activos:\n"
            f"  • {label} ({top_dominance:.0%}, {raw_count} menciones)\n"
            f"  • {sec_label} ({sec_dom:.0%}, {sec_cnt} menciones)\n"
            f"Ambos contextos están activos. Responde desde el que sea más relevante para la pregunta."
        )
        final_tid = f"{top_tid}+{sec_tid2}"
        confidence = "medium"  # colisión siempre reduce certeza
    else:
        hypothesis = _build_hypothesis(top_tid, label, raw_count, confidence)
        final_tid = top_tid

    # Guardar en cache
    _cache[user_id] = (time.time(), hypothesis)

    logger.debug(
        "Intention projected | user=%s | topic=%s | dominance=%.2f | conf=%s | collision=%s",
        user_id[:20], final_tid, top_dominance, confidence,
        collision_topic is not None,
    )

    return IntentionProjection(
        topic_id=final_tid,
        label=label if not collision_topic else f"{label} + {_TOPIC_LABELS.get(collision_topic[0], '')}",
        score=top_score,
        confidence=confidence,
        hypothesis=hypothesis,
    )


def _build_hypothesis(tid: str, label: str, count: int, confidence: str) -> str:
    """Genera el texto de hipótesis para inyectar en el prompt."""
    templates = {
        "launch":   f"El usuario está en proceso de {label} de su sistema. "
                    f"Ha mencionado el tema {count} veces recientemente.",
        "build":    f"El usuario está activamente en {label}. "
                    f"Contexto recurrente en {count} mensajes recientes.",
        "team":     f"El usuario está explorando {label}. "
                    f"Tema presente en {count} interacciones recientes.",
        "market":   f"El usuario sigue de cerca {label}. "
                    f"Mencionado en {count} mensajes recientes.",
        "identity": f"El usuario está trabajando en su {label}. "
                    f"Patrón detectado en {count} mensajes.",
        "growth":   f"El usuario está enfocado en {label}. "
                    f"Aparece en {count} interacciones recientes.",
        "product":  f"El usuario está en fase de {label}. "
                    f"Tema recurrente en {count} mensajes.",
        "learning": f"El usuario está profundizando en {label}. "
                    f"Patrón activo en {count} interacciones.",
    }
    base = templates.get(tid, f"El usuario muestra intención activa de {label} ({count} menciones).")
    if confidence == "high":
        return f"[INTENCIÓN DOMINANTE] {base}"
    return f"[Intención detectada] {base}"


def invalidate_cache(user_id: str) -> None:
    """Invalida el cache de un usuario (llamar cuando se guarda nueva memoria)."""
    _cache.pop(user_id, None)
