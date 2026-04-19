"""
Vectrax Response Auditor — Evaluación Pre-Envío
=================================================
Vectrax evalúa su propia respuesta antes de enviarla al usuario.

Si la respuesta falla la auditoría (genérica, sin acción, desalineada),
la reescribe con instrucción forzada antes de enviarla.

Criterios de auditoría:
  1. GENERICIDAD   — menciona "redes sociales", "influencers", teoría vacía
  2. ACCIÓN        — no contiene ningún paso concreto aplicable
  3. ALINEACIÓN    — no hace referencia a datos reales del sistema
  4. RELEVANCIA    — responde algo diferente a lo que se preguntó

Activación:
  - Siempre activo para el creador (tg:2030762343)
  - Activable por usuario vía flag en SQLite

Creado: 2026-04-01
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger("vectrax.response_auditor")

# ---------------------------------------------------------------------------
# Señales de respuesta genérica (heurística rápida — sin LLM)
# ---------------------------------------------------------------------------

_GENERIC_SIGNALS = re.compile(
    r"(?:"
    # Frases de asistente genérico
    r"\b(?:claro|por\s+supuesto|con\s+gusto|encantado|es\s+un\s+placer)\b"
    r"|\b(?:espero\s+(?:que\s+)?(?:esto\s+)?(?:te\s+)?(?:ayude|sea\s+útil|sirva))\b"
    r"|\b(?:no\s+dudes\s+en|si\s+necesitas\s+(?:más\s+)?ayuda)\b"
    r"|\b(?:con\s+mucho\s+gusto|estar[eé]\s+aquí\s+para)\b"
    # Marketing genérico
    r"|\b(?:redes\s+sociales|influencers?|marketing\s+digital|publicidad\s+online)\b"
    r"|\b(?:estrategia\s+de\s+(?:marketing|contenido|crecimiento))\b"
    r"|\b(?:webinars?|eventos\s+virtuales?)\b"
    # Verborrea
    r"|\b(?:es\s+importante\s+(?:que|destacar|mencionar|tener\s+en\s+cuenta))\b"
    r"|\b(?:como\s+(?:model|asistente|IA|sistema)\s+(?:de\s+lenguaje)?)\b"
    r"|\b(?:potenciando|aprovechando|maximizando)\s+(?:el|la|los|las)\b"
    r"|\b(?:en\s+el\s+mundo\s+(?:actual|digital|de\s+hoy))\b"
    r"|a\s+continuación\s+(?:te\s+)?(?:presento|explico|describo|detallo)"
    r")",
    re.IGNORECASE,
)

# Señales de que la respuesta SÍ es concreta y alineada
_CONCRETE_SIGNALS = re.compile(
    r"(?:"
    r"\b(?:\$\d+|\d+\s*(?:usuarios?|interacciones?|equipos?))\b"
    r"|\b(?:stripe|tavily|telegram|vultr|sqlite|postgresql)\b"
    r"|\b(?:/team|/vx|plan\s+(?:pro|team|individual))\b"
    r"|\b(?:\d+/mes|por\s+mes|mensual)\b"
    r"|\b(?:esta\s+semana|hoy|ahora\s+mismo|inmediatamente)\b"
    r")",
    re.IGNORECASE,
)


@dataclass
class AuditResult:
    passed: bool
    issues: List[str]
    generic_score: int     # 0-3: cuántas señales genéricas
    concrete_score: int    # 0-N: cuántas señales concretas
    rewritten: str = ""    # respuesta reescrita (si pasó por LLM)


def audit_fast(response: str) -> AuditResult:
    """
    Auditoría rápida por heurística — sin LLM, <1ms.
    Detecta señales obvias de genericidad.
    """
    generic_hits = len(_GENERIC_SIGNALS.findall(response))
    concrete_hits = len(_CONCRETE_SIGNALS.findall(response))
    issues = []

    if generic_hits >= 2:
        issues.append(f"generic_content ({generic_hits} señales genéricas)")
    if concrete_hits == 0 and len(response.split()) > 30:
        issues.append("no_concrete_data (sin números ni referencias reales)")
    if len(response.split()) > 150 and concrete_hits == 0:
        issues.append("too_long_no_value (respuesta larga sin datos concretos)")

    passed = len(issues) == 0
    return AuditResult(
        passed=passed,
        issues=issues,
        generic_score=generic_hits,
        concrete_score=concrete_hits,
    )


# ---------------------------------------------------------------------------
# Reescritura forzada vía LLM cuando falla la auditoría
# ---------------------------------------------------------------------------

_REWRITE_PROMPT = """AUDITORÍA FALLIDA. La siguiente respuesta fue evaluada como genérica o desalineada.

PROBLEMAS DETECTADOS: {issues}

RESPUESTA ORIGINAL (RECHAZADA):
{original}

CONTEXTO DEL SISTEMA (usa esto):
{system_context}

PREGUNTA ORIGINAL DEL USUARIO: {query}

INSTRUCCIÓN: Reescribe la respuesta de forma concreta, directa y alineada con el sistema real.
- Usa datos reales del contexto (usuarios, precios, funciones activas)
- Sin teoría genérica, sin "redes sociales", sin "webinars"
- Máximo 5 frases. Cada frase debe agregar valor concreto.
- Si no tienes datos suficientes, di exactamente qué falta — no inventes.

RESPUESTA REESCRITA:"""


def rewrite_response(
    original: str,
    query: str,
    issues: List[str],
    system_context: str = "",
    lang: str = "es",
) -> str:
    """
    Reescribe una respuesta que falló la auditoría usando el LLM.
    Retorna la respuesta mejorada o la original si falla.
    """
    prompt = _REWRITE_PROMPT.format(
        issues=", ".join(issues),
        original=original[:600],
        system_context=system_context[:800],
        query=query[:200],
    )

    # Intelligence Bridge
    try:
        from vectrax.intelligence_bridge import is_ready, route_single
        if is_ready():
            result = route_single(prompt)
            if result.get("success") and result.get("content"):
                rewritten = result["content"].strip()
                logger.info(
                    "Auditor rewrite via Intelligence Bridge | len=%d → %d",
                    len(original), len(rewritten),
                )
                return rewritten
    except Exception as exc:
        logger.debug("Intelligence Bridge unavailable for rewrite: %s", exc)

    # OpenAI directo
    try:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return original
        import httpx
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
                "max_tokens": 400,
                "temperature": 0.3,
            },
            timeout=12.0,
        )
        resp.raise_for_status()
        rewritten = resp.json()["choices"][0]["message"]["content"].strip()
        logger.info(
            "Auditor rewrite via OpenAI | len=%d → %d",
            len(original), len(rewritten),
        )
        return rewritten
    except Exception as exc:
        logger.debug("OpenAI rewrite failed: %s", exc)

    return original


# ---------------------------------------------------------------------------
# Auditor completo — heurística + reescritura si necesario
# ---------------------------------------------------------------------------

def run_audit(
    response: str,
    query: str,
    user_id: str = "",
    system_context: str = "",
    lang: str = "es",
    force: bool = False,
) -> str:
    """
    Ejecuta la auditoría completa sobre una respuesta.

    Flujo:
      1. Auditoría rápida por heurística
      2. Si falla → reescribir con LLM + contexto del sistema
      3. Retornar respuesta mejorada

    Args:
      response:       la respuesta generada por el pipeline
      query:          el mensaje original del usuario
      user_id:        para verificar si el modo auditor está activo
      system_context: contexto del sistema para la reescritura
      lang:           idioma del usuario
      force:          forzar auditoría sin verificar activación

    Returns:
      Respuesta auditada (original si pasa, reescrita si falla).
    """
    if not response or not response.strip():
        return response

    # Verificar si el modo auditor está activo para este usuario
    if not force and not _auditor_active(user_id):
        return response

    result = audit_fast(response)

    if result.passed:
        logger.debug("Audit PASSED | user=%s | concrete=%d", user_id[:20], result.concrete_score)
        return response

    logger.info(
        "Audit FAILED | user=%s | issues=%s | generic=%d concrete=%d",
        user_id[:20], result.issues, result.generic_score, result.concrete_score,
    )

    # Obtener contexto del sistema si no se pasó
    if not system_context:
        try:
            from vectrax.self_context import build_self_context
            system_context = build_self_context(lang=lang)
        except Exception:
            pass

    rewritten = rewrite_response(
        original=response,
        query=query,
        issues=result.issues,
        system_context=system_context,
        lang=lang,
    )

    if rewritten and rewritten != response:
        return rewritten

    return response


# ---------------------------------------------------------------------------
# Activación por usuario
# ---------------------------------------------------------------------------

_AUDITOR_ACTIVE: set = set()  # user_ids con auditor activo en sesión
_CREATOR_ID = "tg:2030762343"


def activate_auditor(user_id: str) -> None:
    """Activa el modo auditor para un usuario."""
    _AUDITOR_ACTIVE.add(user_id)
    logger.info("Auditor activated for %s", user_id[:20])


def deactivate_auditor(user_id: str) -> None:
    """Desactiva el modo auditor para un usuario."""
    _AUDITOR_ACTIVE.discard(user_id)
    logger.info("Auditor deactivated for %s", user_id[:20])


def _auditor_active(user_id: str) -> bool:
    """Retorna True si el auditor está activo para este usuario."""
    return user_id in _AUDITOR_ACTIVE


def is_auditor_active(user_id: str) -> bool:
    """API pública para verificar estado del auditor."""
    return _auditor_active(user_id)
