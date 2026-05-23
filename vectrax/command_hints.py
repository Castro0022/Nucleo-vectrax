"""
Vectrax — Contextual Command Hints
=====================================
Detects user intent and suggests relevant commands ONLY when the
context naturally calls for them. Never dumps command lists.

Principio: los comandos se revelan por contexto, no por dump.
El usuario habla natural → Vectrax responde natural + hint si aplica.

Ejemplo:
  "quiero hacer seguimiento a un cliente" → hint: /lead add nombre
  "necesito crear un equipo"              → hint: /team new nombre
  "cuáles son mis leads"                  → hint: /lead summary

El hint se añade como línea final de la respuesta, separado.
Si no hay match, no se añade nada.

Creado: 2026-05-23
Creador: Mario Bravo Castro
Co-Authored-By: Oz <oz-agent@warp.dev>
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger("vectrax.command_hints")


# ---------------------------------------------------------------------------
# Patrones de intención → hint de comando
# ---------------------------------------------------------------------------
# Cada entrada: (regex_pattern, hint_text_es, hint_text_en)
# El regex se aplica al mensaje del usuario (lowercase).
# Solo se devuelve EL PRIMER match — una sola sugerencia por mensaje.

_HINT_PATTERNS = [
    # --- Leads / seguimiento ---
    (
        re.compile(
            r"(?:seguimiento|follow.?up|tracking|rastrear|monitorear"
            r"|agregar.?(?:un\s+)?(?:cliente|lead|contacto|prospecto)"
            r"|guardar.?(?:un\s+)?(?:cliente|lead|contacto)"
            r"|nuevo.?(?:cliente|lead|contacto|prospecto)"
            r"|add.?(?:a\s+)?(?:new\s+)?(?:client|lead|contact|prospect))",
            re.I,
        ),
        "/lead add nombre",
        "/lead add name",
    ),
    (
        re.compile(
            r"(?:ver.?(?:mis\s+)?(?:leads?|clientes?|contactos?|prospectos?)"
            r"|mis.?(?:leads?|clientes?|seguimientos?)"
            r"|lista.?de.?(?:leads?|clientes?)"
            r"|(?:list|show).?(?:my\s+)?(?:leads?|clients?|contacts?))",
            re.I,
        ),
        "/lead summary",
        "/lead summary",
    ),
    (
        re.compile(
            r"(?:mensaje.?(?:de\s+)?seguimiento"
            r"|qué.?(?:le\s+)?(?:digo|escribo|mando)"
            r"|follow.?up.?message|what.?(?:should\s+)?(?:i\s+)?(?:say|write|send))",
            re.I,
        ),
        "/lead follow nombre",
        "/lead follow name",
    ),

    # --- Equipos ---
    (
        re.compile(
            r"(?:crear.?(?:un\s+)?equipo"
            r"|armar.?(?:un\s+)?equipo"
            r"|nuevo.?equipo"
            r"|create.?(?:a\s+)?team"
            r"|start.?(?:a\s+)?team"
            r"|new.?team)",
            re.I,
        ),
        "/team new nombre",
        "/team new name",
    ),
    (
        re.compile(
            r"(?:unirme.?a.?(?:un\s+)?equipo"
            r"|entrar.?(?:a\s+|en\s+)?(?:un\s+)?equipo"
            r"|join.?(?:a\s+)?team"
            r"|tengo.?(?:un\s+)?código)",
            re.I,
        ),
        "/team join código",
        "/team join code",
    ),

    # --- Recordatorios / tareas ---
    (
        re.compile(
            r"(?:recordar(?:me)?.?(?:que|de)?\s+\w+"
            r"|(?:pon(?:me)?|programa)\s+(?:una?\s+)?(?:alarma|recordatorio)"
            r"|(?:remind|schedule|alarm)\s+\w+)",
            re.I,
        ),
        "Puedes decirlo natural: 'recuérdame mañana llamar a Carlos'",
        "Just say it naturally: 'remind me tomorrow to call Carlos'",
    ),

    # --- Privacidad / datos ---
    (
        re.compile(
            r"(?:mis.?datos|mi.?privacidad|qué.?(?:guardan|almacenan|saben)"
            r"|borrar.?(?:mis\s+)?datos|delete.?(?:my\s+)?data"
            r"|privacy|data.?sovereignty)",
            re.I,
        ),
        "/privacy",
        "/privacy",
    ),

    # --- Ayuda ---
    (
        re.compile(
            r"(?:qué.?puedes.?hacer|qué.?haces|capacidades"
            r"|qué.?comandos|what.?(?:can\s+)?you.?do"
            r"|features|help.?me|ayúdame|ayuda)",
            re.I,
        ),
        "/help",
        "/help",
    ),

    # --- Estado del sistema ---
    (
        re.compile(
            r"(?:estado.?del.?sistema|system.?status"
            r"|cómo.?(?:estás|vas|funciona)"
            r"|how.?are.?you.?(?:doing|running))",
            re.I,
        ),
        "/vx stats",
        "/vx stats",
    ),

    # --- Mercado / crypto ---
    (
        re.compile(
            r"(?:precio|cotización|valor).+(?:btc|bitcoin|eth|crypto)"
            r"|(?:btc|bitcoin|eth|crypto).+(?:precio|price|cotización)",
            re.I,
        ),
        "Puedes escribir directamente: btc, eth, sol",
        "Just type: btc, eth, sol",
    ),
]


def detect_command_hint(
    user_message: str,
    lang: str = "es",
) -> Optional[str]:
    """
    Detect if the user's message implies a command and return a hint.

    Returns:
        A short hint string (e.g. "/lead add nombre") or None.
    """
    text = user_message.strip().lower()

    # Skip if user already used a command
    if text.startswith("/"):
        return None

    # Skip very short messages (greetings, confirmations)
    if len(text.split()) < 3:
        return None

    for pattern, hint_es, hint_en in _HINT_PATTERNS:
        if pattern.search(text):
            hint = hint_en if lang == "en" else hint_es
            logger.debug("command_hint: matched → %s", hint)
            return hint

    return None


def append_hint_to_response(
    response: str,
    hint: str,
) -> str:
    """
    Append a command hint to the response as a subtle footer.
    Only adds if the hint isn't already mentioned in the response.
    """
    if not response or not hint:
        return response

    # Don't duplicate if the command is already in the response
    # Extract the /command part from the hint
    cmd_match = re.search(r"(/\w+(?:\s+\w+)?)", hint)
    if cmd_match and cmd_match.group(1) in response:
        return response

    return f"{response}\n\n💡 {hint}"
