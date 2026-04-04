"""
Vectrax Nucleus Resolver — Respond from accumulated knowledge.

Before calling the LLM, this module checks if Vectrax already has
knowledge about the topic. If patterns from multiple users converge
on the same meaning, the nucleus can synthesize its own answer.

Pipeline:
  1. Embed the user's question
  2. Measure distance to nucleus centroid
  3. If close (< NUCLEUS_RESOLVE_THRESHOLD): search similar patterns
  4. If enough high-quality patterns found: synthesize answer from them
  5. Return synthesized answer or None (→ fall through to LLM)

The nucleus doesn't replace the LLM — it responds when it KNOWS.
When it doesn't know, it stays silent and lets the LLM handle it.

Creado: 2026-04-04
Creador: Mario Bravo Castro (via Oz)
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger("vectrax.nucleus_resolver")

# Distance to centroid below which the nucleus "knows" the topic
NUCLEUS_RESOLVE_THRESHOLD = 0.40

# Minimum patterns needed to synthesize an answer
MIN_PATTERNS_FOR_SYNTHESIS = 3

# Maximum patterns to include in synthesis context
MAX_SYNTHESIS_PATTERNS = 8

# Minimum similarity for a pattern to be considered relevant
PATTERN_RELEVANCE_THRESHOLD = 0.55


# Questions the nucleus should NEVER try to answer
# (temporal, real-time data — the main LLM has context for these)
_SKIP_PATTERNS = re.compile(
    r"(?:"
    r"\b(?:qu[eé]\s+(?:hora|d[ií]a|fecha)|what\s+(?:time|day|date)|quelle\s+heure)\b"
    r"|\b(?:hora\s+(?:es|actual)|time\s+(?:is\s+it|now)|heure\s+est)\b"
    r"|\b(?:cu[aá]ndo|when\s+is|qu[eé]\s+d[ií]a\s+es)\b"
    r"|\b(?:hoy\s+es|today\s+is|estamos\s+a)\b"
    r")",
    re.IGNORECASE,
)


def resolve_from_nucleus(
    question: str,
    user_id: str = "",
) -> Optional[str]:
    """
    Try to resolve a question from the nucleus's accumulated knowledge.

    Returns a synthesized answer string, or None if the nucleus
    doesn't have enough knowledge on this topic.
    """
    # Skip temporal/real-time questions — the main LLM has the context
    if _SKIP_PATTERNS.search(question):
        logger.info("Nucleus: skipping temporal question → main LLM")
        return None

    try:
        return _resolve(question, user_id)
    except Exception as exc:
        logger.debug("Nucleus resolver failed (passthrough): %s", exc)
        return None


def _resolve(question: str, user_id: str) -> Optional[str]:
    from vectrax.embeddings import embed, cosine_similarity, decode_embedding
    from vectrax.core_nucleus import get_core_centroid
    from vectrax import db

    # 1. Embed the question
    q_vec = embed(question)

    # 2. Distance to nucleus centroid
    centroid = get_core_centroid()
    if centroid is None:
        return None

    distance = float(1.0 - np.dot(q_vec, centroid))
    logger.info(
        "Nucleus distance: %.4f (threshold=%.2f)",
        distance, NUCLEUS_RESOLVE_THRESHOLD,
    )

    if distance > NUCLEUS_RESOLVE_THRESHOLD:
        return None  # Nucleus doesn't know enough about this topic

    # 3. Search for relevant patterns across ALL users
    all_stars = db.get_all_user_stars()
    relevant: List[Tuple[str, float, str]] = []  # (content, similarity, user_id)

    for star in all_stars:
        patterns = db.get_patterns_for_user(star.user_id, limit=50)
        for p in patterns:
            if p.embedding is None:
                continue
            p_vec = decode_embedding(p.embedding)
            sim = cosine_similarity(q_vec, p_vec)
            if sim >= PATTERN_RELEVANCE_THRESHOLD:
                relevant.append((p.content, sim, star.user_id))

    if len(relevant) < MIN_PATTERNS_FOR_SYNTHESIS:
        return None  # Not enough evidence

    # 4. Sort by relevance, take top N
    relevant.sort(key=lambda x: -x[1])
    top = relevant[:MAX_SYNTHESIS_PATTERNS]

    # 5. Synthesize — combine the most relevant patterns into a coherent answer
    # Count how many distinct users contributed
    contributors = len({uid for _, _, uid in top})

    # Build synthesis from accumulated knowledge
    knowledge_lines = []
    for content, sim, uid in top:
        # Truncate long patterns
        short = content[:200].strip()
        if short:
            knowledge_lines.append(short)

    if not knowledge_lines:
        return None

    # Determine user's language
    lang = "es"
    try:
        from core.language_gate import get_user_language
        lang = get_user_language(user_id, question)
    except Exception:
        pass

    # Synthesize via LLM with nucleus knowledge as PRIMARY context
    synthesis_prompt = _build_synthesis_prompt(
        question, knowledge_lines, contributors, lang,
    )

    try:
        answer = _call_synthesis_llm(synthesis_prompt)
        if answer:
            logger.info(
                "Nucleus resolved: distance=%.4f | %d patterns from %d users | %d chars",
                distance, len(top), contributors, len(answer),
            )
            return answer
    except Exception as exc:
        logger.debug("Nucleus synthesis LLM failed: %s", exc)

    return None


def _build_synthesis_prompt(
    question: str,
    knowledge: List[str],
    contributors: int,
    lang: str,
) -> str:
    """Build a prompt that instructs the LLM to answer FROM the knowledge."""
    knowledge_block = "\n".join(f"- {k}" for k in knowledge)

    if lang == "es":
        return (
            f"Eres Vectrax. Tienes conocimiento acumulado de {contributors} fuentes.\n"
            f"Responde la siguiente pregunta USANDO SOLO el conocimiento que ya tienes.\n"
            f"No inventes. Si el conocimiento no cubre la pregunta completamente, "
            f"di lo que sabes y admite lo que no.\n\n"
            f"CONOCIMIENTO ACUMULADO:\n{knowledge_block}\n\n"
            f"PREGUNTA: {question}\n\n"
            f"RESPUESTA (directa, 2-4 líneas):"
        )
    elif lang == "fr":
        return (
            f"Tu es Vectrax. Tu as des connaissances accumulées de {contributors} sources.\n"
            f"Réponds à la question suivante EN UTILISANT UNIQUEMENT les connaissances que tu as.\n"
            f"N'invente pas.\n\n"
            f"CONNAISSANCES ACCUMULÉES:\n{knowledge_block}\n\n"
            f"QUESTION: {question}\n\n"
            f"RÉPONSE (directe, 2-4 lignes):"
        )
    else:
        return (
            f"You are Vectrax. You have accumulated knowledge from {contributors} sources.\n"
            f"Answer the following question USING ONLY the knowledge you already have.\n"
            f"Do not invent. If the knowledge doesn't fully cover the question, "
            f"say what you know and admit what you don't.\n\n"
            f"ACCUMULATED KNOWLEDGE:\n{knowledge_block}\n\n"
            f"QUESTION: {question}\n\n"
            f"ANSWER (direct, 2-4 lines):"
        )


def _call_synthesis_llm(prompt: str) -> str:
    """Call the LLM with the synthesis prompt. Returns answer or empty string."""
    # Try OpenAI first
    import os
    import httpx

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if api_key:
        try:
            r = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 300,
                },
                timeout=15.0,
            )
            if r.status_code == 200:
                data = r.json()
                choices = data.get("choices", [])
                if choices:
                    return (choices[0].get("message", {}).get("content", "")).strip()
        except Exception:
            pass

    # Fallback: Ollama local
    try:
        r = httpx.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "llama3.2:3b",
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3},
            },
            timeout=15.0,
        )
        if r.status_code == 200:
            return r.json().get("response", "").strip()
    except Exception:
        pass

    return ""
