"""
Gravity Activator — Pre-Router Constellation Activation
=========================================================
Capa que se ejecuta ANTES del SmartRouter. Detecta palabras con masa
gravitacional alta en el input del usuario y activa sus constelaciones
asociadas, inyectando contexto relevante en el pipeline.

Si la masa máxima detectada >= GRAVITY_LOCK_THRESHOLD (0.85), emite
gravity_lock=True que prohíbe al SmartRouter usar búsqueda web (ONLINE).

Esto previene el caso "Vectrax Bandsaw": la palabra "vectrax" tiene
mass=0.98 en el índice global, por lo que cualquier mensaje que la
contenga JAMÁS será enviado a Tavily.

Creado: 2026-06-08
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.gravity_activator")


@dataclass
class GravityActivation:
    """A single activated word with its constellation."""
    word: str
    mass: float
    scope: str
    category: str
    constellation_snippets: List[str] = field(default_factory=list)


@dataclass
class GravityResult:
    """Result of gravity activation for a message."""
    activated: bool = False
    lock_internal: bool = False       # True = prohibir ONLINE
    max_mass: float = 0.0
    max_word: str = ""
    activations: List[GravityActivation] = field(default_factory=list)
    context: str = ""                 # gravity context to inject
    latency_ms: float = 0.0


# Max patterns to load per activated word
_MAX_PATTERNS_PER_WORD = 5
# Max total context chars
_MAX_CONTEXT_CHARS = 1500


def activate_gravity(
    content: str,
    user_id: str,
    max_activations: int = 3,
) -> GravityResult:
    """
    Activate word gravity for a user message.

    Pipeline:
      1. Extract keywords from content
      2. Look up each keyword in WGI (user scope first, then global)
      3. For words above activation threshold, load constellation
      4. Build gravity context
      5. Determine if gravity_lock should be set

    Args:
        content: User message text
        user_id: User identifier (for per-user polysemy)
        max_activations: Max number of words to activate

    Returns:
        GravityResult with context and lock signal
    """
    t0 = time.perf_counter()
    result = GravityResult()

    try:
        from core.word_gravity import (
            GRAVITY_ACTIVATION_THRESHOLD,
            GRAVITY_LOCK_THRESHOLD,
            extract_keywords,
            get_effective_mass,
            get_constellation_ids,
            record_activation,
            seed_global_index,
        )

        # Ensure seed exists on first call
        seed_global_index()

        keywords = extract_keywords(content)
        if not keywords:
            result.latency_ms = (time.perf_counter() - t0) * 1000
            return result

        # Look up mass for each keyword
        word_masses: List[Tuple[str, float, str]] = []
        for kw in keywords:
            mass, scope = get_effective_mass(kw, user_id)
            if mass >= GRAVITY_ACTIVATION_THRESHOLD:
                word_masses.append((kw, mass, scope))

        if not word_masses:
            result.latency_ms = (time.perf_counter() - t0) * 1000
            return result

        # Sort by mass descending, take top N
        word_masses.sort(key=lambda x: x[1], reverse=True)
        top_words = word_masses[:max_activations]

        result.activated = True
        result.max_mass = top_words[0][1]
        result.max_word = top_words[0][0]
        result.lock_internal = result.max_mass >= GRAVITY_LOCK_THRESHOLD

        # Load constellations for each activated word
        for word, mass, scope in top_words:
            activation = GravityActivation(
                word=word,
                mass=mass,
                scope=scope,
                category=_get_category(word, scope),
            )

            # Load constellation content
            cids = get_constellation_ids(word, scope)
            if cids:
                snippets = _load_constellation_content(cids)
                activation.constellation_snippets = snippets[:_MAX_PATTERNS_PER_WORD]

            result.activations.append(activation)

            # Record activation
            try:
                record_activation(word, scope)
            except Exception:
                pass

        # Build context string
        result.context = _build_gravity_context(result)

        logger.info(
            "GRAVITY | activated=%d | max=%s(%.2f) | lock=%s | "
            "context_len=%d | user=%s",
            len(result.activations),
            result.max_word,
            result.max_mass,
            result.lock_internal,
            len(result.context),
            user_id[:20],
        )

    except Exception as exc:
        logger.debug("Gravity activation failed: %s", exc)

    result.latency_ms = (time.perf_counter() - t0) * 1000
    return result


def _get_category(word: str, scope: str) -> str:
    """Get the category of a word from the index."""
    try:
        from core.word_gravity import get_word_entry
        entry = get_word_entry(word, scope)
        return entry.category if entry else "concept"
    except Exception:
        return "concept"


def _load_constellation_content(
    constellation_ids: List[str],
    limit: int = _MAX_PATTERNS_PER_WORD,
) -> List[str]:
    """
    Load content from constellation IDs (pattern IDs or star IDs).

    Tries patterns table first, then stars table.
    Returns list of content strings, ordered by value_score desc.
    """
    if not constellation_ids:
        return []

    snippets: List[str] = []

    try:
        import sqlite3
        import os

        db_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "vault", "user_memory.db",
        )
        conn = sqlite3.connect(db_path, timeout=2)

        # Try patterns first (they have value_score for ranking)
        placeholders = ",".join("?" for _ in constellation_ids[:limit * 2])
        try:
            rows = conn.execute(
                f"SELECT content, value_score FROM patterns "
                f"WHERE id IN ({placeholders}) "
                f"ORDER BY value_score DESC LIMIT ?",
                (*constellation_ids[:limit * 2], limit),
            ).fetchall()
            for content, _score in rows:
                if content and len(content) > 10:
                    # Truncate long content
                    snippet = content[:200].strip()
                    if len(content) > 200:
                        snippet += "..."
                    snippets.append(snippet)
        except Exception:
            pass

        # If not enough from patterns, try stars
        if len(snippets) < limit:
            remaining = limit - len(snippets)
            try:
                rows = conn.execute(
                    f"SELECT content FROM stars "
                    f"WHERE id IN ({placeholders}) "
                    f"ORDER BY gravity_score DESC LIMIT ?",
                    (*constellation_ids[:limit * 2], remaining),
                ).fetchall()
                for (content,) in rows:
                    if content and len(content) > 10:
                        snippet = content[:200].strip()
                        if len(content) > 200:
                            snippet += "..."
                        snippets.append(snippet)
            except Exception:
                pass

        conn.close()
    except Exception as exc:
        logger.debug("Constellation content load failed: %s", exc)

    return snippets


def _build_gravity_context(result: GravityResult) -> str:
    """
    Build a context block from gravity activations.

    Format:
    [GRAVITY CONTEXT — activated by "word" (mass=X.XX)]
    - snippet 1
    - snippet 2
    ...
    """
    if not result.activations:
        return ""

    parts: List[str] = []
    total_chars = 0

    for activation in result.activations:
        header = (
            f"[GRAVITY — \"{activation.word}\" "
            f"(mass={activation.mass:.2f}, {activation.category})]"
        )
        parts.append(header)
        total_chars += len(header)

        for snippet in activation.constellation_snippets:
            line = f"• {snippet}"
            if total_chars + len(line) > _MAX_CONTEXT_CHARS:
                break
            parts.append(line)
            total_chars += len(line)

        if total_chars >= _MAX_CONTEXT_CHARS:
            break

    return "\n".join(parts)
