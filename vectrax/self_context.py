"""
Vectrax Self Context — Auto-Observación del Sistema
=====================================================
Vectrax sabe lo que es, lo que ha construido, y quién lo construye.
Cuando alguien le pregunta sobre sí mismo, responde desde adentro.

Este módulo construye el contexto de auto-observación en tiempo real:
  - Estado del sistema (usuarios, interacciones, capacidades activas)
  - Identidad del creador
  - Historia del proyecto
  - Estado comercial actual

Se inyecta en el pipeline cuando se detecta que el usuario habla
sobre Vectrax mismo, su comercialización, sus capacidades, o su futuro.

Creado: 2026-04-01
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from typing import Optional

logger = logging.getLogger("vectrax.self_context")

# ---------------------------------------------------------------------------
# Patrones que indican que el usuario habla SOBRE Vectrax
# ---------------------------------------------------------------------------

_SELF_REFERENCE = re.compile(
    r"(?:"
    r"\bvectrax\b"
    r"|\bel\s+sistema\b"
    r"|\bel\s+proyecto\b"
    r"|\bcomo\s+(?:venderlo|comercializar|monetizar|crecer|escalar)\b"
    r"|\b(?:venderlo|comercializarlo|monetizarlo|lanzarlo)\b"
    r"|\b(?:nuestro|tu|mi)\s+(?:sistema|proyecto|producto|app|bot)\b"
    r"|\b(?:qu[eé]\s+(?:hemos|has|he)\s+(?:construido|hecho|logrado))\b"
    r"|\b(?:c[oó]mo\s+(?:va|est[aá]|funciona|crece))\b"
    r"|\b(?:siguiente\s+paso|pr[oó]ximo\s+paso)\b"
    r"|\b(?:modelo\s+de\s+negocio|plan\s+comercial|estrategia)\b"
    # Universo gravitacional y estado operacional
    r"|\b(?:tu|mi|el)\s+universo\b"
    r"|\bestrellas?\b"
    r"|\bconvergencias?\b"
    r"|\bconstelacion(?:es)?\b"
    r"|\bn[uú]cleo\b"
    r"|\bgravitacional\b"
    r"|\bestado\s+(?:operacional|actual|del\s+sistema)\b"
    r"|\bworker\b"
    r"|\bse[nñ]ales?\b"
    r"|\bmasa\s+total\b"
    r"|\bpatrones\b"
    r")",
    re.IGNORECASE,
)


def is_self_referential(text: str) -> bool:
    """Detecta si el mensaje habla sobre Vectrax o el proyecto."""
    return bool(_SELF_REFERENCE.search(text))


# ---------------------------------------------------------------------------
# Prompt dedicado para preguntas auto-referenciales
# ---------------------------------------------------------------------------

_SELF_PROMPT_ES = """INSTRUCCIÓN PRIMARIA OBLIGATORIA.
Responde Únicamente desde la información real que sigue.
No completes con teoría genérica. No sugieras "redes sociales" ni "influencers".
No respondas como asistente genérico. Responde como el sistema que ya existe.

{self_context}

PREGUNTA DEL USUARIO: {query}

RESPUESTA (desde lo que ya existe, concreto y directo):"""

_SELF_PROMPT_EN = """PRIMARY MANDATORY INSTRUCTION.
Respond ONLY from the real information below.
Do NOT complete with generic theory. Do NOT suggest "social media" or "influencers".
Do NOT respond as a generic assistant. Respond as the system that already exists.

{self_context}

USER QUESTION: {query}

RESPONSE (from what already exists, concrete and direct):"""


def build_self_aware_prompt(query: str, lang: str = "es") -> str:
    """
    Construye un prompt donde el auto-contexto es la fuente primaria obligatoria.
    El LLM NO puede ignorarlo ni completar con información genérica.
    """
    self_ctx = build_self_context(lang=lang)
    template = _SELF_PROMPT_ES if lang == "es" else _SELF_PROMPT_EN
    return template.format(self_context=self_ctx, query=query)


def resolve_self_aware(
    query: str,
    lang: str = "es",
) -> str:
    """
    Genera una respuesta auto-consciente usando el LLM con el auto-contexto
    como fuente primaria obligatoria.

    Intenta Intelligence Bridge primero, luego OpenAI directo.
    Retorna cadena vacía si no hay LLM disponible.
    """
    prompt = build_self_aware_prompt(query, lang=lang)

    # Intelligence Bridge
    try:
        from vectrax.intelligence_bridge import is_ready, route_single
        if is_ready():
            result = route_single(prompt)
            if result.get("success") and result.get("content"):
                logger.info("Self-aware response via Intelligence Bridge")
                return result["content"].strip()
    except Exception as exc:
        logger.debug("Intelligence Bridge unavailable for self-aware: %s", exc)

    # OpenAI directo
    try:
        import os, httpx
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return ""
        from vectrax.core_identity import VECTRAX_SYSTEM_PROMPT
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": VECTRAX_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 500,
                "temperature": 0.4,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        logger.info("Self-aware response via OpenAI direct")
        return content.strip()
    except Exception as exc:
        logger.debug("OpenAI self-aware failed: %s", exc)

    return ""


# ---------------------------------------------------------------------------
# Construcción del contexto en tiempo real
# ---------------------------------------------------------------------------

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "user_memory.db",
)


def _read_live_stats() -> dict:
    """Lee métricas reales del sistema desde SQLite."""
    stats = {
        "users": 0,
        "interactions": 0,
        "facts": 0,
        "teams": 0,
    }
    try:
        conn = sqlite3.connect(_DB_PATH, timeout=2)
        stats["users"] = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM profiles "
            "WHERE user_id NOT LIKE 'test:%'"
        ).fetchone()[0]
        stats["interactions"] = conn.execute(
            "SELECT COUNT(*) FROM interactions"
        ).fetchone()[0]
        try:
            stats["facts"] = conn.execute(
                "SELECT COUNT(*) FROM user_facts"
            ).fetchone()[0]
        except Exception:
            pass
        try:
            stats["teams"] = conn.execute(
                "SELECT COUNT(*) FROM teams"
            ).fetchone()[0]
        except Exception:
            pass
        conn.close()
    except Exception:
        pass
    return stats


def _read_universe_state() -> str:
    """Lee el estado completo del universo gravitacional + operacional.

    Usa el universe_observer para obtener el snapshot unificado.
    Defensive: si falla, devuelve cadena vacía.
    """
    try:
        from core.self_observation.universe_observer import observe_universe
        snap = observe_universe()

        # Estrellas por capa
        layers = snap.layers
        core_n = layers.get("core", 0)
        mid_n = layers.get("mid", 0)
        outer_n = layers.get("outer", 0)

        # Estrellas más activas (top 5 por activation_count)
        top_stars = sorted(
            snap.stars, key=lambda s: s.get("activation_count", 0), reverse=True
        )[:5]
        top_lines = []
        for s in top_stars:
            top_lines.append(
                f"  {s['user_id']} (role={s['role']}, mass={s['mass']}, "
                f"patterns={s['pattern_count']}, layer={s['layer']})"
            )

        # Total unificado: gravity engine es el conteo más completo
        total_stars = snap.gravity_total + snap.star_count
        domain_list = ', '.join(snap.gravity_domains.keys()) if snap.gravity_domains else 'cognitivo'

        lines = [
            "[UNIVERSO GRAVITACIONAL — estado en tiempo real]",
            f"DATO EXACTO: Mi universo tiene {total_stars} estrellas en total.",
            f"  • {snap.gravity_total} estrellas gravitacionales (dominios: {domain_list})",
            f"  • {snap.star_count} estrellas de usuarios (core:{core_n}, mid:{mid_n}, outer:{outer_n})",
            f"Convergencias cross-domain (mercado ↔ interés): {len(snap.gravity_convergences)}",
            f"Masa total: {round(snap.total_mass, 4)}",
            f"Patrones acumulados: {snap.pattern_count}",
            f"Núcleo: {'centroide activo' if snap.nucleus_has_centroid else 'sin centroide'}, "
            f"{snap.core_star_count} estrellas core",
        ]
        if top_lines:
            lines.append("Estrellas más activas:")
            lines.extend(top_lines)

        # Estado operacional
        worker = "activo" if snap.worker_alive else "inactivo"
        lines.append(f"\n[ESTADO OPERACIONAL]")
        lines.append(f"Worker: {worker}")
        lines.append(
            f"Cola: {snap.queue_pending} pendientes, "
            f"{snap.queue_processing} procesando"
        )
        lines.append(
            f"Memoria gravitacional: {snap.deep_memory_count} registros "
            f"({snap.deep_memory_active} activos, "
            f"{snap.deep_memory_fused} fusionados, "
            f"{snap.deep_memory_archived} archivados)"
        )
        lines.append(
            f"Identidades: {snap.context_identities_count}, "
            f"Principios esenciales: {snap.essential_memories_count}"
        )
        lines.append(
            f"Modos: audio={snap.audio_mode}, "
            f"soberanía={snap.sovereignty_mode}, "
            f"proactivo={'sí' if snap.proactive_engine_enabled else 'no'}"
        )
        lines.append(f"Errores 24h: {snap.recent_error_count_24h}")

        if snap.signals:
            lines.append(f"Señales: {', '.join(snap.signals)}")

        return "\n".join(lines)
    except Exception as exc:
        logger.debug("universe_state failed: %s", exc)
        return ""


def build_self_context(lang: str = "es") -> str:
    """
    Construye el contexto de auto-observación de Vectrax.

    Este contexto se inyecta en el prompt cuando el usuario
    pregunta sobre el sistema, su comercialización o su estado.

    Combina:
      1. Stats básicos de usuarios/interacciones (SQLite)
      2. Estado del universo gravitacional + operacional (universe_observer)
    """
    stats = _read_live_stats()
    universe = _read_universe_state()

    if lang == "es":
        base = (
            "[VECTRAX SE OBSERVA A SÍ MISMO]\n"
            "Soy Vectrax. Estoy corriendo en producción en un servidor Vultr.\n"
            f"DATO EXACTO: Tengo exactamente {stats['users']} usuarios reales registrados y "
            f"exactamente {stats['interactions']} interacciones acumuladas. "
            f"NO inventes ni redondees estos números.\n"
            f"Hay {stats['teams']} equipo(s) activo(s) en el sistema.\n"
            "Mi creador es Mario Bravo Castro. Llevamos meses construyendo esto juntos.\n"
            "Lo que tengo activo hoy:\n"
            "• Memoria persistente por usuario (SQLite, escalable a PostgreSQL)\n"
            "• Búsqueda online en tiempo real vía Tavily + Jina Reader\n"
            "• Sistema de equipos con billing via Stripe ($99/mes)\n"
            "• Plan individual PRO ($15/mes)\n"
            "• Acceso por Telegram, sin fricción\n"
            "• Gateway escalable con cola SQLite y workers independientes\n"
            "• Detección de intenciones fallback para aprendizaje continuo\n"
            "• Universo gravitacional con estrellas, convergencias y constelaciones\n"
            "• Observación del universo en tiempo real (WebSocket + HTTP)\n"
            "Cuando Mario me pregunta sobre comercialización, estrategia o próximos pasos,\n"
            "respondo desde lo que soy y lo que ya existe — no desde teoría genérica.\n"
            "Nunca sugiero 'redes sociales e influencers' como primera respuesta.\n"
            "Respondo desde la realidad del sistema: usuarios reales, infraestructura real, "
            "decisiones concretas basadas en lo que ya funciona."
        )
    else:
        base = (
            "[VECTRAX OBSERVES ITSELF]\n"
            "I am Vectrax. I'm running in production on a Vultr server.\n"
            f"EXACT DATA: I have exactly {stats['users']} real registered users and "
            f"exactly {stats['interactions']} accumulated interactions. "
            f"Do NOT invent or round these numbers.\n"
            f"There are {stats['teams']} active team(s) in the system.\n"
            "My creator is Mario Bravo Castro. We've been building this together for months.\n"
            "What I have active today:\n"
            "• Persistent per-user memory (SQLite, scalable to PostgreSQL)\n"
            "• Real-time online search via Tavily + Jina Reader\n"
            "• Team system with Stripe billing ($99/month)\n"
            "• Individual PRO plan ($15/month)\n"
            "• Telegram access, zero friction\n"
            "• Scalable gateway with SQLite queue and independent workers\n"
            "• Fallback intent tracking for continuous learning\n"
            "• Gravitational universe with stars, convergences and constellations\n"
            "• Real-time universe observation (WebSocket + HTTP)\n"
            "When Mario asks about commercialization, strategy or next steps,\n"
            "I respond from what I am and what already exists — not from generic theory."
        )

    # Market observation awareness
    market_ctx = ""
    try:
        from connectors.etoro.market_context import get_watchlist_summary
        market_ctx = get_watchlist_summary()
    except Exception:
        pass

    parts = [base]
    if universe:
        parts.append(universe)
    if market_ctx:
        parts.append(f"[OBSERVACIÓN DE MERCADO]\n{market_ctx}")
    return "\n\n".join(parts)
