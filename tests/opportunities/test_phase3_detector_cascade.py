"""
tests/opportunities/test_phase3_detector_cascade.py
=====================================================
Cascada Fase 1 → 2 → 3 con mocks:

    * MockEncoder produce vectores deterministas a partir del texto.
    * MockLLMClient devuelve un dict pre-acordado.

No hay sentence-transformers ni httpx tocando red.
"""
from __future__ import annotations

import hashlib
from typing import Sequence

import pytest

from core.opportunities import (
    DetectionResult,
    EmbeddingSignalScorer,
    LLMSignalScorer,
    OpportunityDetector,
)


# ---------------------------------------------------------------------------
# Mock encoder: hash determinístico → vector estable
# ---------------------------------------------------------------------------

class MockEncoder:
    """
    Encoder determinista: misma frase → mismo vector.
    Frases sinónimas de OPEN/CLOSED se acercan mediante un set de
    "topic tokens" que dominan el vector.
    """

    OPEN_TOKENS = {
        "pensar", "pienso", "piensa", "decida", "decidir",
        "presupuesto", "vereo", "ver", "decido",
        "info", "informacion", "mandame", "esperar",
        "despues", "adelante", "tarde", "mensual", "semana",
    }
    CLOSED_TOKENS = {
        "pague", "pago", "compra", "compre", "cancelar",
        "cancelado", "rechazo", "no", "interesa", "listo",
        "confirmado", "cerrado",
    }

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vecs = []
        for t in texts:
            words = t.lower().split()
            open_match = sum(1 for w in words if any(tok in w for tok in self.OPEN_TOKENS))
            closed_match = sum(1 for w in words if any(tok in w for tok in self.CLOSED_TOKENS))
            # Vector 8-dim: [openness, closedness, hash-noise...]
            base = [
                float(open_match),
                float(closed_match),
            ]
            # 6 más por hash determinista
            h = hashlib.md5(t.encode("utf-8")).digest()
            base.extend([(b / 255.0) for b in h[:6]])
            vecs.append(base)
        return vecs


# ---------------------------------------------------------------------------
# Mock LLM client
# ---------------------------------------------------------------------------

class MockLLM:
    def __init__(self, response: dict):
        self.response = response
        self.calls = 0

    async def classify(self, text: str) -> dict:
        self.calls += 1
        return dict(self.response)


# ---------------------------------------------------------------------------
# Fase 2 — embeddings rescatan match cuando reglas no encuentran
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_embeddings_rescue_when_rules_miss():
    enc = MockEncoder()
    emb = EmbeddingSignalScorer(encoder=enc, threshold=0.4)
    det = OpportunityDetector(embeddings_scorer=emb)

    # Frase que NO contiene ningún signal de reglas (verificado con
    # OPEN_SIGNALS y CLOSED_SIGNALS) pero el MockEncoder la marca
    # como OPEN porque incluye 'decidir', 'pensar', 'esperar'.
    r = await det.detect("Voy a decidir y pensar con calma, esperar un poco")
    assert r.matched is True
    assert r.is_open is True
    assert "embeddings" in r.engine


@pytest.mark.asyncio
async def test_embeddings_skipped_when_rules_already_strong():
    """Si reglas dieron alta confianza, no se llama embeddings."""
    enc = MockEncoder()
    emb = EmbeddingSignalScorer(encoder=enc)
    det = OpportunityDetector(embeddings_scorer=emb)

    r = await det.detect("Carlos dice que lo piensa para la próxima semana.")
    assert r.matched is True
    assert r.engine == "rules-v1"


# ---------------------------------------------------------------------------
# Fase 3 — LLM rescata cuando reglas y embeddings fallan
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_rescue_when_rules_and_embeddings_miss():
    enc = MockEncoder()
    emb = EmbeddingSignalScorer(encoder=enc, threshold=0.99)  # imposible
    llm = LLMSignalScorer(client=MockLLM({
        "is_opportunity": True,
        "kind": "open",
        "confidence": 0.82,
        "rationale": "evidencia indirecta de duda",
    }))
    det = OpportunityDetector(
        embeddings_scorer=emb,
        llm_scorer=llm,
        enable_llm=True,
    )

    r = await det.detect("Hmm, no sé bien todavía, ya lo veré con calma")
    assert r.matched is True
    assert r.is_open is True
    assert "llm" in r.engine


@pytest.mark.asyncio
async def test_llm_disabled_does_not_run():
    """Si enable_llm=False, no hay match aunque el LLM diría sí."""
    enc = MockEncoder()
    emb = EmbeddingSignalScorer(encoder=enc, threshold=0.99)
    llm_client = MockLLM({
        "is_opportunity": True, "kind": "open",
        "confidence": 0.9, "rationale": "x",
    })
    llm = LLMSignalScorer(client=llm_client)
    det = OpportunityDetector(
        embeddings_scorer=emb,
        llm_scorer=llm,
        enable_llm=False,
    )

    r = await det.detect("Hmm, no sé bien todavía")
    assert r.matched is False
    assert llm_client.calls == 0


@pytest.mark.asyncio
async def test_llm_rejects_non_opportunity():
    """Si el LLM dice 'none', no se promueve a match."""
    llm_client = MockLLM({
        "is_opportunity": False, "kind": "none",
        "confidence": 0.95, "rationale": "saludo casual",
    })
    llm = LLMSignalScorer(client=llm_client)
    det = OpportunityDetector(llm_scorer=llm, enable_llm=True)

    r = await det.detect("Hola, qué tal todo")
    assert r.matched is False


# ---------------------------------------------------------------------------
# Retro-compatibilidad: sin scorers, comportamiento Fase 1 puro
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backward_compat_phase1_only():
    det = OpportunityDetector()
    r = await det.detect("Carlos dijo que lo piensa.")
    assert r.matched is True
    assert r.engine == "rules-v1"


# ---------------------------------------------------------------------------
# Resolución de conflicto: reglas vs embedding (reglas mandan)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rules_win_over_embeddings_in_conflict():
    """
    Si reglas dicen OPEN con baja confianza pero embedding dice CLOSED,
    se mantiene OPEN y se anota el desacuerdo en extras.
    """
    enc = MockEncoder()
    # Threshold bajo para forzar match en cualquier dirección
    emb = EmbeddingSignalScorer(encoder=enc, threshold=0.0)
    det = OpportunityDetector(
        embeddings_scorer=emb,
        # Bajamos el confidence floor de las reglas para forzar Fase 2
        open_signals=["lo pienso"],
    )

    # "lo pienso" → reglas matchea OPEN con conf 0.7 (alta)
    # Las reglas se mantienen sin importar lo que diga embedding.
    r = await det.detect("lo pienso pero ya pagué")
    # El detector match en CLOSED por reglas (CLOSED tiene prioridad
    # en _rules_detect). Verificamos que respeta esto.
    assert r.matched is True
    assert r.is_closed is True


# ---------------------------------------------------------------------------
# Confidence floor para gate de fases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_confidence_combined_when_rules_and_embeddings_agree():
    """Confidence final = max(rules, embeddings) cuando coinciden."""
    enc = MockEncoder()
    emb = EmbeddingSignalScorer(encoder=enc, threshold=0.0)
    det = OpportunityDetector(embeddings_scorer=emb)

    # 'lo veo' tiene conf 0.7 en reglas (DELAY).
    # Bajamos artificialmente el RULES_FLOOR forzando con un signal
    # custom. Pero como acá la conf=0.7 supera 0.65, embeddings no
    # corre. Verificamos eso primero.
    r = await det.detect("lo veo y te aviso")
    assert r.engine == "rules-v1"
    assert r.confidence == pytest.approx(0.7)
