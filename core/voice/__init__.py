"""
Vectrax Voice Engine — Motor de Voz Viva
=========================================
Separa por completo seguridad, identidad, memoria y voz final.

Pipeline canónico (orquestado por VoiceEngine.compose_final):

    user_message
        ↓
    1. intent.classify           → intent + tone
        ↓
    2. persona.build_directive   → directiva contextual para el LLM
        ↓
    3. memory (externa)          → contexto solo si aporta valor
        ↓
    4. LLM call (externo)        → respuesta cruda
        ↓
    5. humanizer.humanize        → corrige solo lo que rompe humanidad
        ↓
    6. audit.audit_without_replacing → señala, NO sustituye
        ↓
    final response

El filtro no escribe la personalidad. El filtro protege.
La voz la escribe el motor.

Reglas duras:
  - El fallback nunca suena técnico.
  - El filtro nunca reemplaza una respuesta válida por texto robótico.
  - Las preguntas casuales reciben respuesta casual.
  - Las preguntas técnicas pueden activar tono técnico.
  - Las preguntas de identidad reciben respuesta simple y humana.
  - La memoria aparece solo si aporta valor.

Creador: Mario Bravo Castro
Fecha:   2026-05-06
"""

from core.voice.voice_engine import (  # noqa: F401
    VoiceEngine,
    classify_tone,
    build_voice_directive,
    humanize_response,
    fallback_response,
    audit_without_replacing,
    Intent,
    Tone,
    AuditResult,
)

__all__ = [
    "VoiceEngine",
    "classify_tone",
    "build_voice_directive",
    "humanize_response",
    "fallback_response",
    "audit_without_replacing",
    "Intent",
    "Tone",
    "AuditResult",
]
