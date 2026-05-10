"""
core/opportunities/embeddings.py
==================================
Fase 2 del detector: scoring semántico contra exemplars.

NO reemplaza las reglas — actúa sólo cuando las reglas no encontraron
match exacto. Usa cosine similarity contra un set de frases prototipo
(exemplars) etiquetadas como OPEN o CLOSED.

Diseño:
    * `Encoder` es un Protocol — permite swap por mock en tests o
      reemplazo por cualquier encoder semántico (sentence-transformers,
      OpenAI embeddings, hash-vectorizer, etc.) sin tocar el detector.
    * Carga lazy del modelo real: el import de sentence-transformers
      se difiere hasta que alguien llame a `encode()` por primera vez.
    * Embeddings de exemplars cacheados in-memory tras la primera
      computación.

Por defecto opt-in: el detector usa este scorer solo si recibe una
instancia en su constructor.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Sequence, runtime_checkable


logger = logging.getLogger("vectrax.opportunities.embeddings")


# ---------------------------------------------------------------------------
# Exemplars semilla — fácil de extender
# ---------------------------------------------------------------------------

OPEN_EXEMPLARS: List[str] = [
    "te aviso cuando lo decida",
    "lo voy a pensar",
    "déjame ver el presupuesto",
    "me lo pienso para la próxima semana",
    "está un poco caro para mí",
    "quizás más adelante",
    "hablamos en unos días",
    "mándame la información por favor",
    "lo reviso y te confirmo",
    "tal vez el mes que viene",
]

CLOSED_EXEMPLARS: List[str] = [
    "ya pagué la factura",
    "no me interesa, gracias",
    "decidimos cancelar",
    "está cerrado el tema",
    "perfecto, lo tomo",
    "ya lo compré",
    "confirmado, listo",
]


# ---------------------------------------------------------------------------
# Encoder Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class Encoder(Protocol):
    """Cualquier objeto que sepa producir vectores numéricos por texto."""

    def encode(self, texts: Sequence[str]) -> "list[list[float]]":
        ...


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------

@dataclass
class EmbeddingScore:
    matched: bool = False
    is_open: bool = False
    is_closed: bool = False
    best_exemplar: str = ""
    confidence: float = 0.0
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Default lazy encoder usando sentence-transformers
# ---------------------------------------------------------------------------

class SentenceTransformerEncoder:
    """
    Wrapper lazy. La librería pesada se importa en el primer `encode()`,
    no al import del módulo. Si sentence-transformers no está instalada,
    `encode()` lanza `RuntimeError` claro y el detector cae a fallback.
    """

    def __init__(
        self,
        model_name: str = "paraphrase-multilingual-MiniLM-L12-v2",
    ) -> None:
        self._model_name = model_name
        self._model = None  # carga diferida

    def encode(self, texts: Sequence[str]) -> "list[list[float]]":
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore
            except Exception as exc:  # pragma: no cover
                raise RuntimeError(
                    "sentence-transformers no instalado: "
                    + str(exc)
                ) from exc
            logger.info(
                "Loading sentence-transformer model: %s", self._model_name,
            )
            self._model = SentenceTransformer(self._model_name)
        # convert_to_numpy=True devuelve np.ndarray; lo casteamos a list
        # para no exponer numpy en la API.
        vectors = self._model.encode(
            list(texts), convert_to_numpy=True, normalize_embeddings=True,
        )
        return [list(map(float, v)) for v in vectors]


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity sin numpy. Asume vectores no-cero."""
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    import math
    return dot / (math.sqrt(na) * math.sqrt(nb))


class EmbeddingSignalScorer:
    """
    Scorer semántico de señales. No conoce el dominio del detector —
    solo dice "¿este texto se parece más a un exemplar OPEN o CLOSED?".

    Args:
        encoder: cualquier objeto que cumpla el `Encoder` Protocol.
        threshold: similarity mínima para considerar match.
        open_exemplars / closed_exemplars: override de los defaults.
    """

    def __init__(
        self,
        encoder: Encoder,
        threshold: float = 0.55,
        open_exemplars: Optional[Sequence[str]] = None,
        closed_exemplars: Optional[Sequence[str]] = None,
    ) -> None:
        self._encoder = encoder
        self._threshold = float(threshold)
        self._open = list(open_exemplars or OPEN_EXEMPLARS)
        self._closed = list(closed_exemplars or CLOSED_EXEMPLARS)
        # Cache lazy de embeddings de exemplars
        self._open_vecs: Optional[List[List[float]]] = None
        self._closed_vecs: Optional[List[List[float]]] = None

    def _ensure_exemplar_vecs(self) -> None:
        if self._open_vecs is None:
            self._open_vecs = self._encoder.encode(self._open)
        if self._closed_vecs is None:
            self._closed_vecs = self._encoder.encode(self._closed)

    async def score(self, text: str) -> EmbeddingScore:
        """
        Devuelve el match más cercano. Si la mejor similarity supera
        el threshold, marca matched=True.
        """
        if not text or not text.strip():
            return EmbeddingScore()

        try:
            self._ensure_exemplar_vecs()
        except Exception as exc:
            logger.debug("encoder unavailable: %s", exc)
            return EmbeddingScore()

        try:
            vec = self._encoder.encode([text])[0]
        except Exception as exc:
            logger.debug("encode failed: %s", exc)
            return EmbeddingScore()

        # Best match contra OPEN
        best_open_sim = -1.0
        best_open_ex = ""
        for ex_vec, ex_text in zip(self._open_vecs or [], self._open):
            sim = _cosine(vec, ex_vec)
            if sim > best_open_sim:
                best_open_sim = sim
                best_open_ex = ex_text

        # Best match contra CLOSED
        best_closed_sim = -1.0
        best_closed_ex = ""
        for ex_vec, ex_text in zip(self._closed_vecs or [], self._closed):
            sim = _cosine(vec, ex_vec)
            if sim > best_closed_sim:
                best_closed_sim = sim
                best_closed_ex = ex_text

        # Decide: el de mayor similarity gana, si supera threshold
        if max(best_open_sim, best_closed_sim) < self._threshold:
            return EmbeddingScore(
                matched=False,
                confidence=max(best_open_sim, best_closed_sim),
                extra={
                    "best_open_sim": best_open_sim,
                    "best_closed_sim": best_closed_sim,
                },
            )

        if best_closed_sim >= best_open_sim:
            return EmbeddingScore(
                matched=True,
                is_closed=True,
                best_exemplar=best_closed_ex,
                confidence=best_closed_sim,
                extra={
                    "best_open_sim": best_open_sim,
                    "best_closed_sim": best_closed_sim,
                },
            )
        return EmbeddingScore(
            matched=True,
            is_open=True,
            best_exemplar=best_open_ex,
            confidence=best_open_sim,
            extra={
                "best_open_sim": best_open_sim,
                "best_closed_sim": best_closed_sim,
            },
        )
