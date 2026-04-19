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
            "SELECT COUNT(DISTINCT user_id) FROM profiles"
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


def build_self_context(lang: str = "es") -> str:
    """
    Construye el contexto de auto-observación de Vectrax.

    Este contexto se inyecta en el prompt cuando el usuario
    pregunta sobre el sistema, su comercialización o su estado.
    """
    stats = _read_live_stats()

    if lang == "es":
        return (
            "[VECTRAX SE OBSERVA A SÍ MISMO]\n"
            "Soy Vectrax. Estoy corriendo en producción en un servidor Vultr.\n"
            f"Tengo {stats['users']} usuarios registrados y "
            f"{stats['interactions']} interacciones acumuladas.\n"
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
            "Cuando Mario me pregunta sobre comercialización, estrategia o próximos pasos,\n"
            "respondo desde lo que soy y lo que ya existe — no desde teoría genérica.\n"
            "Nunca sugiero 'redes sociales e influencers' como primera respuesta.\n"
            "Respondo desde la realidad del sistema: usuarios reales, infraestructura real, "
            "decisiones concretas basadas en lo que ya funciona."
        )
    else:
        return (
            "[VECTRAX OBSERVES ITSELF]\n"
            "I am Vectrax. I'm running in production on a Vultr server.\n"
            f"I have {stats['users']} registered users and "
            f"{stats['interactions']} accumulated interactions.\n"
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
            "When Mario asks about commercialization, strategy or next steps,\n"
            "I respond from what I am and what already exists — not from generic theory."
        )
