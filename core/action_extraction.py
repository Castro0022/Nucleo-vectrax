"""
core/action_extraction.py — Diseño A/B/C, Frente C.

Traduce un ACTION_REQUEST ya resuelto (`speech_act == "action"`, ver
`core.intent_ssot`) en parámetros ESTRUCTURADOS, antes de que la integración
(`vectrax.integrations.place_search`, `core.scheduler`) reciba nada. Ninguna
integración vuelve a interpretar el mensaje conversacional completo ni
mantiene su propia colección de prefijos que eliminar.

Mecanismo (única llamada LLM de salida estructurada, JSON, temperature=0):
NO hay lista de cocinas, verbos ni prefijos enumerados. "Quiero comer
italiano cerca" se resuelve a `category=restaurant, cuisine=italian` porque
el LLM INFIERE el significado ("comer italiano" implica restaurante), no
porque exista una tabla cocina→categoría. Es el mismo patrón de dos niveles
ya aceptado en este repo (`vectrax/fact_memory.py::_extract_facts_llm`):
mecanismo determinista primero (SSOT + capability_status deciden SI se debe
actuar), LLM estructurado solo para el QUÉ, una vez ya autorizado.

Esta llamada combina, en un solo turno, (a) confirmar el DOMINIO de la
acción (place_search | reminder | none) y (b) si aplica, extraer sus
parámetros. Esto es una simplificación deliberada frente al diseño
originalmente propuesto (un detector de dominio "reminder" por embeddings,
análogo al de `place_search` en `core.intent_ssot`): se calibró
empíricamente contra los casos reales y las anclas de "recordatorio" no
separaban con margen suficiente del vocabulario de auto-observación
("qué has observado últimamente" puntuaba más alto que "recuérdame X") con
el modelo de embeddings disponible — un falso positivo real, no aceptable.
La extracción LLM generaliza igual (o mejor, por comprensión de significado)
sin ese riesgo, al costo de una llamada adicional, acotada a mensajes que YA
se clasificaron como `speech_act == "action"` (una minoría del tráfico).

Autoridad: este módulo NUNCA decide si actuar — eso ya lo decidió
`core.intent_ssot` (QUÉ/CÓMO se dice) y `core.self_observation.capability_status`
(si la capacidad está confirmada). Este módulo solo traduce texto libre a
parámetros tipados, y únicamente debe invocarse DESPUÉS de que ambas
autoridades ya autorizaron la ejecución.

Creador: Mario Bravo Castro
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional, Union

logger = logging.getLogger("vectrax.action_extraction")


# ---------------------------------------------------------------------------
# Contrato de salida — dataclasses por capacidad, no texto libre
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlaceSearchParams:
    """Parámetros ya resueltos para PLACE_SEARCH. `category` es un tipo de
    lugar en inglés (vocabulario de la Places API), nunca el texto crudo del
    usuario. `location` es `"near_user"` (usa la ubicación guardada) o un
    texto de ubicación explícito que el usuario mencionó."""
    category: str
    cuisine: str = ""
    location: str = "near_user"


@dataclass(frozen=True)
class ReminderParams:
    """Parámetros ya resueltos para REMINDER. `content` es el recordatorio
    limpio (sin ruido conversacional); `when_text` es la expresión temporal
    tal como la dijo el usuario ("mañana", "el viernes a las 7am"), resuelta
    después por el parser de fecha que YA tiene `core.scheduler` — no se
    reinventa el parseo de fechas aquí."""
    content: str
    when_text: str


ActionParams = Union[PlaceSearchParams, ReminderParams]

# Dominios de acción reconocidos por esta etapa. Cerrado a propósito: nuevas
# capacidades accionables se añaden aquí explícitamente, no se infieren.
_KNOWN_DOMAINS = ("place_search", "reminder")


_EXTRACTION_SYSTEM_PROMPT = (
    "Extraes intención estructurada de mensajes conversacionales. "
    "Respondes EXCLUSIVAMENTE con JSON válido, sin explicación, sin markdown, "
    "sin texto antes ni después."
)

_EXTRACTION_PROMPT_TEMPLATE = """Analiza el mensaje del usuario y determina si pide UNA de estas acciones. Responde SOLO con un objeto JSON, nada más.

Si pide buscar/encontrar un lugar físico, negocio, comida o servicio (aunque no use la palabra "buscar"):
{{"domain": "place_search", "category": "<tipo de lugar en inglés: restaurant, pharmacy, gym, bank, etc.>", "cuisine": "<tipo de cocina en inglés si aplica, o cadena vacía>", "location": "near_user"}}

Si pide que le recuerden, agenden o avisen algo en un momento futuro:
{{"domain": "reminder", "content": "<qué debe recordarse, sin la fecha/hora>", "when_text": "<la expresión de tiempo EXACTA tal como la escribió el usuario>"}}

Si el mensaje no pide ninguna de las dos acciones anteriores:
{{"domain": "none"}}

Mensaje del usuario: "{content}"

JSON:"""


def _parse_llm_json(raw: str) -> Optional[dict]:
    """Parsea la respuesta del LLM a dict. Tolera bloques ```json ... ``` y
    texto residual antes/después de las llaves. Nunca lanza."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception as exc:
        logger.debug("action_extraction: JSON parse failed: %s", exc)
        return None


def _build_place_params(data: dict) -> Optional[PlaceSearchParams]:
    category = str(data.get("category", "") or "").strip().lower()
    if not category:
        return None
    cuisine = str(data.get("cuisine", "") or "").strip().lower()
    location = str(data.get("location", "") or "near_user").strip() or "near_user"
    return PlaceSearchParams(category=category, cuisine=cuisine, location=location)


def _build_reminder_params(data: dict) -> Optional[ReminderParams]:
    content = str(data.get("content", "") or "").strip()
    when_text = str(data.get("when_text", "") or "").strip()
    if not content and not when_text:
        return None
    return ReminderParams(content=content or "recordatorio", when_text=when_text or "mañana")


def extract_action_params(
    content: str,
    lang: str = "es",
    hint_intent: str = "",
) -> Optional[ActionParams]:
    """Extrae parámetros estructurados de un ACTION_REQUEST ya autorizado.

    Args:
        content: mensaje conversacional completo del usuario (única vez que
            este módulo lo lee — el resultado tipado es lo único que viaja
            de aquí en adelante).
        lang: idioma del usuario (informativo; el prompt de extracción es
            agnóstico de idioma de entrada, siempre pide JSON en inglés).
        hint_intent: `primary_intent` ya resuelto por el SSOT, si se conoce
            (p.ej. "place_search"). No obliga el resultado — es una pista,
            nunca un atajo que se salte la inferencia del LLM — porque el
            propio SSOT puede no conocer el dominio (caso "reminder", ver
            docstring del módulo).

    Devuelve `None` si el LLM no está disponible, si el mensaje no pide
    ninguna acción reconocida, o si la respuesta no es JSON utilizable —
    nunca lanza. El llamador NUNCA debe ejecutar una integración con el
    texto crudo cuando esto devuelve `None`.
    """
    if not content or not content.strip():
        return None
    try:
        from core.llm_call import complete
        prompt = _EXTRACTION_PROMPT_TEMPLATE.format(content=content.strip()[:500])
        res = complete(
            prompt,
            system_prompt=_EXTRACTION_SYSTEM_PROMPT,
            max_tokens=150,
            temperature=0.0,
            timeout=10.0,
        )
        if not res.ok or not res.text:
            logger.debug(
                "action_extraction: LLM unavailable (status=%s, hint=%s)",
                res.status, hint_intent,
            )
            return None
        data = _parse_llm_json(res.text)
        if not data:
            return None
        domain = str(data.get("domain", "") or "").strip().lower()
        if domain not in _KNOWN_DOMAINS:
            return None
        if domain == "place_search":
            return _build_place_params(data)
        if domain == "reminder":
            return _build_reminder_params(data)
        return None
    except Exception as exc:
        logger.debug("action_extraction: extraction failed (passthrough): %s", exc)
        return None
