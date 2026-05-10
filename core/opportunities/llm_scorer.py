"""
core/opportunities/llm_scorer.py
==================================
Fase 3 del detector: scoring por LLM como desempate.

Solo se invoca cuando ni reglas ni embeddings resolvieron con
confianza suficiente. Costo controlado.

Diseño:
    * Protocol `LLMClient.classify()` — el scorer no sabe qué modelo
      usa. En producción usa httpx → OpenAI; en tests, un mock.
    * Env-gated: por defecto deshabilitado. Activar con
      `VECTRAX_OPPORTUNITY_LLM_SCORING=1`.
    * Output JSON estructurado: {is_opportunity, kind, confidence}.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable


logger = logging.getLogger("vectrax.opportunities.llm_scorer")


# ---------------------------------------------------------------------------
# Resultado neutral
# ---------------------------------------------------------------------------

@dataclass
class LLMScore:
    matched: bool = False
    is_open: bool = False
    is_closed: bool = False
    confidence: float = 0.0
    rationale: str = ""
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Cliente LLM (Protocol — mockeable en tests)
# ---------------------------------------------------------------------------

@runtime_checkable
class LLMClient(Protocol):
    """Devuelve un dict {is_opportunity, kind, confidence, rationale?}."""

    async def classify(self, text: str) -> dict:
        ...


# ---------------------------------------------------------------------------
# Cliente real basado en httpx + OpenAI (lazy)
# ---------------------------------------------------------------------------

_PROMPT = (
    "Eres un clasificador. Dado un mensaje en español, decide si "
    "expresa una oportunidad comercial ABIERTA (interés/duda/objeción/"
    "demora) o CERRADA (compra confirmada o rechazo definitivo). "
    "Si el mensaje no es ninguna de las dos, devuelve none.\n"
    "Responde SOLO un JSON válido con el formato exacto:\n"
    '{"is_opportunity": true|false, "kind": "open"|"closed"|"none", '
    '"confidence": 0.0-1.0, "rationale": "string corto"}\n'
    "Sin texto adicional, sin markdown, solo el JSON."
)


class OpenAILLMClient:
    """
    Implementación httpx → OpenAI. Lazy: no toca la red hasta que
    `classify()` es llamado. Si falla por cualquier motivo, devuelve
    un dict vacío y deja que el detector continúe.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        timeout_s: float = 8.0,
    ) -> None:
        self._model = (
            model
            or os.environ.get("VECTRAX_OPPORTUNITY_LLM_MODEL", "gpt-4o-mini")
        )
        self._timeout = timeout_s

    async def classify(self, text: str) -> dict:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return {}
        try:
            import httpx  # type: ignore
        except Exception as exc:  # pragma: no cover
            logger.debug("httpx unavailable: %s", exc)
            return {}

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "messages": [
                            {"role": "system", "content": _PROMPT},
                            {"role": "user", "content": text[:1000]},
                        ],
                        "max_tokens": 120,
                        "temperature": 0.0,
                        "response_format": {"type": "json_object"},
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                raw = data["choices"][0]["message"]["content"]
                return json.loads(raw)
        except Exception as exc:
            logger.debug("LLM classify failed: %s", exc)
            return {}


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

def is_llm_scoring_enabled() -> bool:
    """Por defecto OFF. Se activa con env explícito."""
    return os.environ.get(
        "VECTRAX_OPPORTUNITY_LLM_SCORING", "0",
    ).strip().lower() in {"1", "true", "yes", "on"}


class LLMSignalScorer:
    """
    Wrapper alrededor de un `LLMClient` que normaliza la salida a
    `LLMScore`. Robusto a respuestas malformadas: ante cualquier duda
    devuelve `matched=False` y deja que el detector use el resultado
    previo (reglas o embeddings).
    """

    def __init__(
        self,
        client: Optional[LLMClient] = None,
        min_confidence: float = 0.6,
    ) -> None:
        self._client = client or OpenAILLMClient()
        self._min_conf = float(min_confidence)

    async def score(self, text: str) -> LLMScore:
        if not text or not text.strip():
            return LLMScore()
        try:
            data = await self._client.classify(text)
        except Exception as exc:
            logger.debug("LLM scorer client error: %s", exc)
            return LLMScore()

        if not data:
            return LLMScore()

        is_op = bool(data.get("is_opportunity", False))
        kind = str(data.get("kind", "none")).lower()
        try:
            conf = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0

        if not is_op or kind == "none" or conf < self._min_conf:
            return LLMScore(
                matched=False,
                confidence=conf,
                rationale=str(data.get("rationale", ""))[:200],
                extra={"raw": data},
            )

        return LLMScore(
            matched=True,
            is_open=(kind == "open"),
            is_closed=(kind == "closed"),
            confidence=conf,
            rationale=str(data.get("rationale", ""))[:200],
            extra={"raw": data},
        )
