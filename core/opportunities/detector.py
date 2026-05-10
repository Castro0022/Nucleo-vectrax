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
from .embeddings import EmbeddingScore, EmbeddingSignalScorer
from .llm_scorer import LLMScore, LLMSignalScorer, is_llm_scoring_enabled


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

# Confianza mínima por debajo de la cual recurrimos a embeddings.
# Las reglas Fase 1 dan ~0.7–0.85; más bajo de eso queremos validar
# semánticamente.
_RULES_CONFIDENCE_FLOOR = 0.65
# Confianza mínima debajo de la cual el embedding NO se considera
# definitivo y conviene desempatar con LLM (si está habilitado).
_EMBEDDINGS_CONFIDENCE_FLOOR = 0.62


class OpportunityDetector:
    """
    Interfaz async con cascada Fase 1 → 2 → 3:

        1. reglas (siempre, baratas)
        2. embeddings semánticos (si las reglas no resolvieron)
        3. LLM scoring (último recurso, opt-in por env)

    Retro-compatible: si no se pasan scorers, el comportamiento es
    idéntico al de Fase 1. El service/repository no necesitan saber
    qué motor resolvió — reciben siempre `DetectionResult`.
    """

    def __init__(
        self,
        open_signals: Optional[Sequence[str]] = None,
        closed_signals: Optional[Sequence[str]] = None,
        embeddings_scorer: Optional[EmbeddingSignalScorer] = None,
        llm_scorer: Optional[LLMSignalScorer] = None,
        enable_llm: Optional[bool] = None,
    ) -> None:
        self._open = [s.lower() for s in (open_signals or OPEN_SIGNALS)]
        self._closed = [s.lower() for s in (closed_signals or CLOSED_SIGNALS)]
        self._emb = embeddings_scorer
        self._llm = llm_scorer
        # Si no se especifica explícitamente, respeta env
        self._enable_llm = (
            enable_llm if enable_llm is not None
            else is_llm_scoring_enabled()
        )

    # -- API principal ------------------------------------------------------

    async def detect(
        self,
        message: str,
        context: Optional[dict] = None,
    ) -> DetectionResult:
        """
        Cascada Fase 1 → 2 → 3. Cada fase puede vetar/refinar a la
        siguiente, pero nunca degrada un match de alta confianza.
        """
        text = (message or "").strip()
        if not text:
            return DetectionResult(raw_message=text)

        # Fase 1 — reglas
        result = self._rules_detect(text)

        # Si las reglas dieron un match sólido, listo.
        if result.matched and result.confidence >= _RULES_CONFIDENCE_FLOOR:
            return result

        # Fase 2 — embeddings (si hay scorer)
        if self._emb is not None:
            result = await self._embeddings_score(text, context, result)
            if result.matched and result.confidence >= _EMBEDDINGS_CONFIDENCE_FLOOR:
                return result

        # Fase 3 — LLM (opt-in)
        if self._llm is not None and self._enable_llm:
            result = await self._llm_score(text, context, result)

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

    # -- Fase 2: embeddings ------------------------------------------------

    async def _embeddings_score(
        self,
        message: str,
        context: Optional[dict],
        prior: DetectionResult,
    ) -> DetectionResult:
        """
        Si las reglas no matchearon, intenta resolver por similitud
        semántica a exemplars. Si las reglas sí matchearon pero con
        baja confianza, el embedding actúa como confirmador.
        """
        if self._emb is None:
            return prior
        try:
            emb: EmbeddingScore = await self._emb.score(message)
        except Exception as exc:
            logger.debug("embeddings scorer failed: %s", exc)
            return prior

        if not emb.matched:
            return prior

        # Embeddings da un veredicto. Si las reglas no matchearon,
        # promovemos. Si matchearon, refinamos confidence al máximo
        # de ambos pero sin cambiar la clase OPEN/CLOSED si las reglas
        # ya dijeron lo contrario — prioridad a reglas en conflicto.
        if not prior.matched:
            return DetectionResult(
                matched=True,
                is_open=emb.is_open,
                is_closed=emb.is_closed,
                intent_type=(
                    IntentType.CLOSED if emb.is_closed else IntentType.INTEREST
                ),
                matched_signal=emb.best_exemplar,
                contact_name=_extract_contact_name(message),
                confidence=float(emb.confidence),
                raw_message=message,
                engine="embeddings-v1",
                extra={"emb": emb.extra},
            )

        # Conflict resolution: si embedding y reglas discrepan en
        # OPEN/CLOSED, mantenemos reglas pero anotamos.
        if (prior.is_open and emb.is_closed) or (
            prior.is_closed and emb.is_open
        ):
            new_extra = dict(prior.extra or {})
            new_extra["embedding_disagreement"] = emb.extra
            prior.extra = new_extra
            return prior

        # Acuerdo de clase: combinar confidence (max).
        prior.confidence = max(prior.confidence, float(emb.confidence))
        prior.engine = "rules+embeddings"
        return prior

    # -- Fase 3: LLM scoring ----------------------------------------------

    async def _llm_score(
        self,
        message: str,
        context: Optional[dict],
        prior: DetectionResult,
    ) -> DetectionResult:
        """
        Último recurso: validar/clasificar con LLM cuando reglas y
        embeddings no convergen. Solo se invoca si `enable_llm`.
        """
        if self._llm is None:
            return prior
        try:
            llm: LLMScore = await self._llm.score(message)
        except Exception as exc:
            logger.debug("llm scorer failed: %s", exc)
            return prior

        if not llm.matched:
            return prior

        # Si nada matchó antes, el LLM da el veredicto.
        if not prior.matched:
            return DetectionResult(
                matched=True,
                is_open=llm.is_open,
                is_closed=llm.is_closed,
                intent_type=(
                    IntentType.CLOSED if llm.is_closed else IntentType.INTEREST
                ),
                matched_signal="(llm classification)",
                contact_name=_extract_contact_name(message),
                confidence=float(llm.confidence),
                raw_message=message,
                engine="llm-v1",
                extra={"llm_rationale": llm.rationale},
            )

        # Si ya había match previo: usar LLM solo para subir confidence
        # cuando concuerda. Si discrepa, dejamos prior y anotamos.
        if (prior.is_open and llm.is_closed) or (
            prior.is_closed and llm.is_open
        ):
            new_extra = dict(prior.extra or {})
            new_extra["llm_disagreement"] = {
                "kind": "closed" if llm.is_closed else "open",
                "confidence": llm.confidence,
                "rationale": llm.rationale,
            }
            prior.extra = new_extra
            return prior

        prior.confidence = max(prior.confidence, float(llm.confidence))
        prior.engine = (prior.engine + "+llm") if prior.engine else "llm-v1"
        return prior
