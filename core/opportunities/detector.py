"""
core/opportunities/detector.py
================================
Detector de señales de oportunidad. Diseñado para evolución en tres
fases sin reescribir arquitectura ni modificar el resto del sistema:

    Fase 1: reglas (esta versión)
    Fase 2: embeddings semánticos (slot reservado)
    Fase 3: LLM scoring (slot reservado)

Quien consume el detector recibe siempre un `DetectionResult`, así el
service no necesita saber qué motor lo resolvió.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .domain import IntentType


logger = logging.getLogger("vectrax.opportunities.detector")


# ---------------------------------------------------------------------------
# Catálogo de señales (Fase 1)
# ---------------------------------------------------------------------------

OPEN_SIGNALS: List[str] = [
    "te aviso",
    # Pensar (1ª, 3ª persona y futuro—cliente o reportado)
    "lo pienso",
    "lo piensa",
    "lo pensará",
    "lo va a pensar",
    "va a pensarlo",
    "déjame ver",
    "dejame ver",
    "la próxima semana",
    "la proxima semana",
    "mándame info",
    "mandame info",
    "está caro",
    "esta caro",
    "quizás",
    "quizas",
    "hablamos",
    "tal vez",
    "lo veo",
    "lo reviso",
    "más adelante",
    "mas adelante",
]

CLOSED_SIGNALS: List[str] = [
    "ya pagué",
    "ya pague",
    "no me interesa",
    "cancelamos",
    "listo",
    "confirmado",
    "cerrado",
    "ya compré",
    "ya compre",
    "perfecto, lo tomo",
]


# ---------------------------------------------------------------------------
# Resultado neutral
# ---------------------------------------------------------------------------

@dataclass
class DetectionResult:
    """Resultado neutral entre fases. El service decide qué hacer."""
    matched: bool = False
    is_open: bool = False
    is_closed: bool = False
    intent_type: IntentType = IntentType.INTEREST
    matched_signal: str = ""
    contact_name: Optional[str] = None
    confidence: float = 0.0
    raw_message: str = ""
    engine: str = "rules-v1"
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Extracción ligera de nombre de contacto
# ---------------------------------------------------------------------------

# Capitalizado al inicio, posible apellido, y un verbo reflexivo
# o introductor común. Conservador: prioriza precisión sobre recall.
# Sin re.IGNORECASE: el nombre debe ser realmente capitalizado, no
# 'me' en minúscula matcheando por flag case-insensitive.
_NAME_VERB_PATTERN = re.compile(
    r"\b([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?)"
    r"\s+(?:dice|dijo|me dijo|me dice|comenta|comentó|piensa|pensó"
    r"|menciona|mencionó|escribe|escribió|pidió|pide|"
    r"quiere|quería|necesita|necesitaba|cree|creyó|"
    r"lo va a pensar)\b",
)


def _extract_contact_name(message: str) -> Optional[str]:
    if not message:
        return None
    m = _NAME_VERB_PATTERN.search(message)
    if m:
        return m.group(1).strip()
    # Fallback heurístico mínimo: primer token capitalizado de la frase
    # solo si parece nombre (evita "Hola", "Gracias", etc.).
    blacklist = {
        "Hola", "Gracias", "Perfecto", "Bueno", "Listo", "Vale",
        "Si", "Sí", "No", "Ok", "Okay",
    }
    for tok in message.split():
        clean = tok.strip(".,!?¡¿:;()[]\"'")
        if (
            clean
            and clean[0].isupper()
            and len(clean) >= 3
            and clean not in blacklist
        ):
            return clean
    return None


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class OpportunityDetector:
    """
    Interfaz async pensada para futuras fases. Hoy el motor es de
    reglas; las fases 2/3 quedan listas para enchufarse sin tocar el
    service ni el repository.
    """

    def __init__(
        self,
        open_signals: Optional[Sequence[str]] = None,
        closed_signals: Optional[Sequence[str]] = None,
    ) -> None:
        self._open = [s.lower() for s in (open_signals or OPEN_SIGNALS)]
        self._closed = [s.lower() for s in (closed_signals or CLOSED_SIGNALS)]

    # -- Fase 1: reglas ------------------------------------------------------

    async def detect(
        self,
        message: str,
        context: Optional[dict] = None,
    ) -> DetectionResult:
        """
        Devuelve un DetectionResult. Async desde el inicio para que el
        upgrade a embeddings o LLM no requiera cambiar callers.
        """
        text = (message or "").strip()
        if not text:
            return DetectionResult(raw_message=text)

        result = self._rules_detect(text)
        # Slots de fases futuras (no llamados todavía).
        # result = await self._embeddings_score(text, context, result)
        # result = await self._llm_score(text, context, result)
        return result

    # -- Implementación de reglas -------------------------------------------

    def _rules_detect(self, message: str) -> DetectionResult:
        lower = message.lower()
        # CLOSED tiene prioridad: si llega "ya pagué, hablamos en marzo",
        # es cerrado, no abierto.
        for sig in self._closed:
            if sig in lower:
                return DetectionResult(
                    matched=True,
                    is_closed=True,
                    intent_type=IntentType.CLOSED,
                    matched_signal=sig,
                    contact_name=_extract_contact_name(message),
                    confidence=0.85,
                    raw_message=message,
                    engine="rules-v1",
                )
        for sig in self._open:
            if sig in lower:
                intent = self._classify_open_intent(sig)
                return DetectionResult(
                    matched=True,
                    is_open=True,
                    intent_type=intent,
                    matched_signal=sig,
                    contact_name=_extract_contact_name(message),
                    confidence=0.7,
                    raw_message=message,
                    engine="rules-v1",
                )
        return DetectionResult(raw_message=message, engine="rules-v1")

    @staticmethod
    def _classify_open_intent(signal: str) -> IntentType:
        if signal in {"está caro", "esta caro"}:
            return IntentType.OBJECTION
        if signal in {
            "lo pienso", "lo piensa", "lo pensará",
            "lo va a pensar", "va a pensarlo",
            "déjame ver", "dejame ver",
            "quizás", "quizas", "tal vez",
            "la próxima semana", "la proxima semana",
            "más adelante", "mas adelante",
            "lo veo", "lo reviso",
        }:
            return IntentType.DELAY
        if signal in {"te aviso", "hablamos"}:
            return IntentType.FOLLOWUP
        return IntentType.INTEREST

    # -- Slots reservados para fases futuras --------------------------------

    async def _embeddings_score(
        self,
        message: str,
        context: Optional[dict],
        prior: DetectionResult,
    ) -> DetectionResult:
        """Slot reservado: ajustar confidence con embeddings semánticos."""
        return prior

    async def _llm_score(
        self,
        message: str,
        context: Optional[dict],
        prior: DetectionResult,
    ) -> DetectionResult:
        """Slot reservado: scoring LLM como tie-breaker o validador."""
        return prior
