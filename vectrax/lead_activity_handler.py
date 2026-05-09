"""
Vectrax Lead Activity Handler — Capa Unificada
================================================
Canoniza la detección de actividad natural sobre leads
("Hablé con Carlos", "Carlos dice que está caro", etc.) y
produce un ContactUpdate declarativo en lugar de cadenas duras.

Motivación (Clase G — antipatrón handler-hardcoded):
Antes, core/operator/external_gateway.py tenía ~45 líneas inline
con respuestas "Actualizado.\\n...". Eso bypaseaba el LLM y producía
respuestas transaccionales que rompían la fluidez conversacional.

Esta capa:
  • Detecta señales de actividad (contacto / objeción / interés).
  • Persiste el efecto secundario (record_contact, detect_and_store).
  • Devuelve un dict declarativo — el gateway decide si lo responde
    directo o lo inyecta como contexto al LLM.
  • NO produce strings para el usuario. El tono lo da el LLM o la
    identidad central, nunca este módulo.

Contrato:
    process_lead_activity(user_id, text) -> Optional[LeadUpdate]
        donde LeadUpdate es:
            {
              "lead_name": str,
              "kind": "objection_price" | "contact_still_interested"
                      | "contact_registered",
              "context_note": str   # para inyectar al LLM
            }

Ley: un fallo, un motor; nunca un parche.
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.lead_activity_handler")


# Señales declarativas — no tocar sin actualizar tests.
_CONTACT_SIGNALS = (
    "hablé", "hable", "llame", "llamé", "escribí", "escribi",
    "contacté", "contacte", "reuní", "me dijo",
    "respondió", "respondio",
    "spoke", "called", "contacted", "met with",
)

_OBJECTION_SIGNALS = (
    "dice que está caro", "está caro", "muy caro",
    "no tiene presupuesto", "no puede pagar",
    "says it's expensive", "too expensive",
)

_INTEREST_SIGNALS = (
    "interesado", "interested", "quiere", "quieren",
    "le gusta", "avanzar", "sí quiere", "si quiere",
)

# Conversacional — no dispara update aunque se mencione un nombre.
_CONVERSATIONAL_PREFIXES = (
    "hola", "buen día", "buenos días", "buenas", "hey", "hi",
    "qué tal", "que tal", "cómo estás", "como estas",
)


def _looks_conversational(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if "?" in t or t.endswith("?"):
        return True
    if len(t) < 20:
        return True
    tl = t.lower()
    return any(tl.startswith(g) for g in _CONVERSATIONAL_PREFIXES)


def process_lead_activity(user_id: str, text: str) -> Optional[Dict[str, Any]]:
    """
    Detecta actividad natural sobre un lead y persiste el efecto.
    Devuelve un LeadUpdate declarativo, o None si no hay match.

    El caller (gateway) decide cómo presentarlo — idealmente inyectando
    context_note al prompt del LLM, no como string directo al usuario.
    """
    if _looks_conversational(text):
        return None

    try:
        from core.lead_tracker import get_leads, record_contact
        from core.preference_tracker import detect_and_store
    except Exception as exc:  # pragma: no cover
        logger.debug("lead_activity_handler imports failed: %s", exc)
        return None

    try:
        leads: List[Dict[str, Any]] = get_leads(user_id)
    except Exception as exc:
        logger.debug("get_leads failed: %s", exc)
        return None

    if not leads:
        return None

    tl = text.lower()
    for lead in leads:
        lname = lead.get("name", "").lower()
        if not lname or lname not in tl:
            continue

        # Objeción de precio
        if any(s in tl for s in _OBJECTION_SIGNALS):
            try:
                detect_and_store(user_id, text)
                record_contact(user_id, lead["name"])
            except Exception as exc:
                logger.debug("objection persist failed: %s", exc)
            return {
                "lead_name": lead["name"],
                "kind": "objection_price",
                "context_note": (
                    f"[lead_update] Objeción de precio detectada para "
                    f"{lead['name']}. Ya quedó registrado."
                ),
            }

        # Contacto realizado
        if any(s in tl for s in _CONTACT_SIGNALS):
            try:
                record_contact(user_id, lead["name"])
                detect_and_store(user_id, text)
            except Exception as exc:
                logger.debug("contact persist failed: %s", exc)

            still_interested = any(w in tl for w in _INTEREST_SIGNALS)
            if still_interested:
                return {
                    "lead_name": lead["name"],
                    "kind": "contact_still_interested",
                    "context_note": (
                        f"[lead_update] Contacto registrado con "
                        f"{lead['name']}; sigue interesado."
                    ),
                }
            return {
                "lead_name": lead["name"],
                "kind": "contact_registered",
                "context_note": (
                    f"[lead_update] Contacto registrado con "
                    f"{lead['name']}."
                ),
            }

    return None
