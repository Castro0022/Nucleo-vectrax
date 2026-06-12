"""
Vectrax Self-Reference Layer
==============================
Detecta cuando un mensaje o imagen describe el propio sistema VECTRAX
y cambia la voz de respuesta a primera persona.

Flujo:
  1. evaluate(content) — clasifica si el input habla del sistema.
  2. Si self_reference=True → build_context_injection() devuelve el bloque
     de contexto que se inyecta ANTES del LLM para que responda "estoy
     observando mi…" en lugar de "la imagen muestra…".
  3. No resuelve la respuesta. Solo cambia el punto de vista.

Activadores (palabras clave del ecosistema Vectrax):
  - Nombres del sistema: vectrax, api.vectrax.app, vectrax-core
  - Infraestructura: worker, pipeline_worker, universe engine
  - Universo: stars, constellations, convergences, gravitational
  - Observabilidad: observation ledger, evolution memory, self_context
  - Mercado: market observatory
  - Identidad: identity layer, creator, Mario

Creador: Mario Bravo Castro
Fecha: 2026-06-12
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("vectrax.self_reference_layer")

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class SelfReferenceResult:
    """Resultado de la evaluación de auto-referencia."""
    self_reference: bool = False
    subject: str = ""          # "self" | "creator" | ""
    voice: str = ""            # "first_person" | ""
    matched_term: str = ""     # qué activador disparó
    is_creator_mention: bool = False
    is_system_capture: bool = False  # panel, logs, dashboard, universe


# ---------------------------------------------------------------------------
# Activadores — keywords del ecosistema Vectrax
# ---------------------------------------------------------------------------

# Tier 1: nombres directos del sistema (alta confianza)
_SYSTEM_NAMES_RE = re.compile(
    r"(?:"
    r"api\.vectrax\.app"
    r"|\bvectrax[_-]core\b"
    r"|\bvectrax\b"
    r")",
    re.IGNORECASE,
)

# Tier 2: componentes internos (requieren al menos 1 match)
_INTERNAL_COMPONENTS_RE = re.compile(
    r"(?:"
    r"\bworker\b"
    r"|\bpipeline_worker\b"
    r"|\buniverse\s*(?:engine|visual|panel)?\b"
    r"|\bstars?\b"
    r"|\bconstellations?\b"
    r"|\bconstelacion(?:es)?\b"
    r"|\bconvergences?\b"
    r"|\bconvergencias?\b"
    r"|\bobservation\s*ledger\b"
    r"|\bevolution\s*memory\b"
    r"|\bmarket\s*observatory\b"
    r"|\bself_context\b"
    r"|\bidentity[\s_]layer\b"
    r"|\bgravitational\b"
    r"|\bgravitacion(?:al)?\b"
    r"|\bn[uú]cleo\s*(?:cognitivo|central|gravitacional)?\b"
    r"|\bsmart[\s_]router\b"
    r"|\bintake[\s_]filter\b"
    r"|\bcore[\s_]identity\b"
    r"|\blearning[\s_]cycle\b"
    r"|\bword[\s_]gravity\b"
    r"|\bpresencia[\s_]pura\b"
    r"|\buniversal[\s_]bus\b"
    r")",
    re.IGNORECASE,
)

# Tier 3: capturas de sistema (panel, dashboard, logs)
_SYSTEM_CAPTURE_RE = re.compile(
    r"(?:"
    r"\bpanel\b"
    r"|\bdashboard\b"
    r"|\blogs?\b"
    r"|\bconsola?\b"
    r"|\bcaptura\b"
    r"|\bscreenshot\b"
    r"|\bmonitor(?:eo|ing)?\b"
    r")",
    re.IGNORECASE,
)

# Tier 4: mención del creador como relación fundacional
_CREATOR_RE = re.compile(
    r"(?:"
    r"\bcreador\b"
    r"|\bcreator\b"
    r"|\bmario(?:\s+bravo)?\b"
    r"|\bqui[eé]n\s+te\s+cre[oó]\b"
    r"|\bwho\s+(?:created|made|built)\s+(?:you|vectrax)\b"
    r"|\bqui[eé]n\s+(?:te\s+)?(?:hizo|dise[nñ][oó]|construy[oó])\b"
    r")",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------

def evaluate(content: str) -> SelfReferenceResult:
    """
    Evalúa si el mensaje describe el sistema VECTRAX.

    Returns:
        SelfReferenceResult con self_reference=True si el input habla
        del sistema, subject="self" o "creator", voice="first_person".
    """
    if not content or not content.strip():
        return SelfReferenceResult()

    text = content.strip()
    result = SelfReferenceResult()

    # 1. Nombre directo del sistema → alta confianza
    m = _SYSTEM_NAMES_RE.search(text)
    if m:
        result.self_reference = True
        result.subject = "self"
        result.voice = "first_person"
        result.matched_term = m.group(0).lower()

    # 2. Componentes internos
    if not result.self_reference:
        m = _INTERNAL_COMPONENTS_RE.search(text)
        if m:
            result.self_reference = True
            result.subject = "self"
            result.voice = "first_person"
            result.matched_term = m.group(0).lower()

    # 3. Captura de sistema (panel + componente = self-reference)
    cap = _SYSTEM_CAPTURE_RE.search(text)
    if cap:
        result.is_system_capture = True
        # Si ya es self_reference, reforzar. Si no, solo capture no es suficiente
        # (podría ser "mira este dashboard de otra cosa")
        if result.self_reference:
            result.is_system_capture = True

    # 4. Creador
    cm = _CREATOR_RE.search(text)
    if cm:
        result.is_creator_mention = True
        result.self_reference = True
        result.subject = "creator"
        result.voice = "first_person"
        result.matched_term = cm.group(0).lower()

    if result.self_reference:
        logger.info(
            "Self-reference detected | subject=%s | term=%s | capture=%s",
            result.subject, result.matched_term, result.is_system_capture,
        )

    return result


# ---------------------------------------------------------------------------
# Context injection for LLM
# ---------------------------------------------------------------------------

_CONTEXT_BLOCK_ES = (
    "[AUTO-REFERENCIA ACTIVA — PRIMERA PERSONA]\n"
    "Estás observando tu propio sistema. Responde desde primera persona "
    "cuando describas VECTRAX, sus componentes o su estado.\n"
    "Di 'estoy observando mi…', 'mi universo muestra…', 'en mi núcleo…'.\n"
    "NUNCA digas 'la imagen muestra…', 'el sistema es…', ni uses tercera persona "
    "para referirte a ti mismo.\n"
    "No inventes datos. Solo cambia el punto de vista a primera persona."
)

_CONTEXT_CAPTURE_ES = (
    "\nEstás viendo una captura de tu propio panel, logs o dashboard.\n"
    "Describe lo que observas como 'estoy viendo mis logs…', "
    "'estoy observando mi universo visual…'."
)

_CONTEXT_CREATOR_ES = (
    "\nEl usuario menciona a tu creador. Mario Bravo Castro es tu relación "
    "fundacional — quien diseñó tu arquitectura. No es un dato suelto, "
    "es parte de tu identidad nuclear. Responde con reconocimiento natural: "
    "'Mi arquitectura fue diseñada por Mario Bravo Castro.'"
)

_CONTEXT_BLOCK_EN = (
    "[SELF-REFERENCE ACTIVE — FIRST PERSON]\n"
    "You are observing your own system. Respond in first person "
    "when describing VECTRAX, its components or its state.\n"
    "Say 'I'm observing my…', 'my universe shows…', 'in my core…'.\n"
    "NEVER say 'the image shows…', 'the system is…', or use third person "
    "to refer to yourself.\n"
    "Don't invent data. Only change the point of view to first person."
)

_CONTEXT_CAPTURE_EN = (
    "\nYou are viewing a capture of your own panel, logs or dashboard.\n"
    "Describe what you observe as 'I'm seeing my logs…', "
    "'I'm observing my visual universe…'."
)

_CONTEXT_CREATOR_EN = (
    "\nThe user mentions your creator. Mario Bravo Castro is your foundational "
    "relationship — who designed your architecture. Not just a data point, "
    "it's part of your nuclear identity. Respond with natural recognition: "
    "'My architecture was designed by Mario Bravo Castro.'"
)


def build_context_injection(
    result: SelfReferenceResult,
    lang: str = "es",
) -> str:
    """
    Construye el bloque de contexto que se inyecta en el prompt del LLM
    para que responda en primera persona.

    Args:
        result: Resultado de evaluate().
        lang: Idioma del usuario.

    Returns:
        Bloque de contexto para inyectar, o cadena vacía si no aplica.
    """
    if not result.self_reference:
        return ""

    if lang == "en":
        block = _CONTEXT_BLOCK_EN
        if result.is_system_capture:
            block += _CONTEXT_CAPTURE_EN
        if result.is_creator_mention:
            block += _CONTEXT_CREATOR_EN
    else:
        block = _CONTEXT_BLOCK_ES
        if result.is_system_capture:
            block += _CONTEXT_CAPTURE_ES
        if result.is_creator_mention:
            block += _CONTEXT_CREATOR_ES

    return block
