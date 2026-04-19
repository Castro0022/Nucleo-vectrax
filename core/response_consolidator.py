"""
Vectrax Response Consolidator — Capa de Unificación de Salida
===============================================================
Puerta final genérica que unifica, deduplica y consolida toda
respuesta antes de enviarla al usuario.

Problema que resuelve:
  Múltiples rutas activas (semántico, places, online, LLM, fallback,
  memoria) pueden producir contenido parcial o duplicado para la
  misma consulta. Esta capa garantiza una sola respuesta limpia.

Principios:
  1. Una consulta → una respuesta. Nunca bloques duplicados.
  2. Prioridad por fuente: memory > identity > places > online > local > llm
  3. Si una ruta ya resolvió, la respuesta es final.
  4. Si hay fragmentos parciales de múltiples rutas, consolida.
  5. Trazabilidad interna (sources) sin exposición al usuario.
  6. Genérico: funciona para cualquier intent.

Uso:
  consolidator = ResponseConsolidator()
  final = consolidator.consolidate(fragments)
  # → una sola respuesta limpia

Privacidad:
  Solo procesa texto de respuesta. No almacena identidad, credenciales
  ni datos personales.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.response_consolidator")


# ---------------------------------------------------------------------------
# Source Priority — orden de preferencia de fuentes
# ---------------------------------------------------------------------------

class SourcePriority(IntEnum):
    """Prioridad de fuentes de respuesta (menor = mayor prioridad)."""
    MEMORY      = 10    # memoria soberana del usuario
    IDENTITY    = 20    # respuesta de identidad canónica
    PLACES      = 30    # datos reales de Google Places
    ONLINE      = 40    # búsqueda web (datos factuales)
    LOCAL       = 50    # búsqueda en memoria local (estrellas)
    LLM         = 60    # generación por modelo AI
    FALLBACK    = 70    # fallback genérico
    UNKNOWN     = 99


# Mapeo de source_path strings a prioridades
_SOURCE_PRIORITY_MAP: Dict[str, SourcePriority] = {
    "memory": SourcePriority.MEMORY,
    "identity": SourcePriority.IDENTITY,
    "places": SourcePriority.PLACES,
    "online": SourcePriority.ONLINE,
    "local": SourcePriority.LOCAL,
    "local_to_online": SourcePriority.ONLINE,
    "llm": SourcePriority.LLM,
    "ai": SourcePriority.LLM,
    "fallback": SourcePriority.FALLBACK,
}


def get_priority(source: str) -> SourcePriority:
    """Return the priority for a source path string."""
    return _SOURCE_PRIORITY_MAP.get(source, SourcePriority.UNKNOWN)


# ---------------------------------------------------------------------------
# Response Fragment — pieza de respuesta con metadatos
# ---------------------------------------------------------------------------

@dataclass
class ResponseFragment:
    """Un fragmento de respuesta producido por una ruta del pipeline."""
    text: str
    source: str                  # memory, identity, places, online, local, llm
    confidence: float = 0.0
    is_complete: bool = True     # ¿resuelve la consulta por sí solo?
    value_type: str = ""         # value_classifier: memory_store, info_response, etc.

    @property
    def priority(self) -> int:
        return get_priority(self.source)

    @property
    def text_hash(self) -> str:
        """Hash del texto normalizado para detección de duplicados exactos."""
        normalized = re.sub(r"\s+", " ", self.text.strip().lower())
        return hashlib.md5(normalized.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Response Consolidator
# ---------------------------------------------------------------------------

class ResponseConsolidator:
    """
    Consolida múltiples fragmentos de respuesta en una sola salida limpia.

    Reglas:
      1. Si hay un fragmento completo de alta prioridad, es la respuesta.
      2. Eliminar duplicados textuales exactos.
      3. Eliminar duplicados semánticos (>70% solapamiento de palabras).
      4. Si quedan múltiples fragmentos complementarios, fusionar.
      5. Priorizar información factual sobre genérica.
    """

    SEMANTIC_OVERLAP_THRESHOLD = 0.70  # >70% palabras compartidas → duplicado
    MAX_FINAL_LENGTH = 2000            # límite de respuesta consolidada

    def consolidate(
        self,
        fragments: List[ResponseFragment],
        input_text: str = "",
        intent: str = "",
        strategy: str = "",
        user_id: str = "",
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Consolida fragmentos de respuesta en una sola salida.

        Args:
            fragments: Lista de fragmentos producidos por distintas rutas.
            input_text: Texto original del usuario (para clasificación de valor).
            intent: Intent del SmartRoute.
            strategy: Estrategia seleccionada.
            user_id: ID del usuario.

        Returns:
            (final_text, trace) donde trace contiene las fuentes usadas,
            decisiones de consolidación y clasificación de valor.
        """
        trace: Dict[str, Any] = {
            "input_fragments": len(fragments),
            "sources_seen": [],
            "duplicates_removed": 0,
            "strategy": "",
            "value": None,
        }

        if not fragments:
            trace["strategy"] = "empty_input"
            return "", trace

        # Filtrar fragmentos vacíos
        fragments = [f for f in fragments if f.text and f.text.strip()]
        if not fragments:
            trace["strategy"] = "all_empty"
            return "", trace

        trace["sources_seen"] = [f.source for f in fragments]

        # --- 1. Si hay exactamente un fragmento, retornar directo ---
        if len(fragments) == 1:
            trace["strategy"] = "single_fragment"
            final_text = fragments[0].text.strip()
            trace["value"] = self._classify_and_register_value(
                final_text, input_text, intent, strategy, user_id,
            )
            return final_text, trace

        # --- 2. Ordenar por prioridad (menor = mejor) ---
        fragments.sort(key=lambda f: f.priority)

        # --- 3. Si el de mayor prioridad es completo, usarlo ---
        best = fragments[0]
        if best.is_complete and best.priority <= SourcePriority.ONLINE:
            trace["strategy"] = f"best_complete:{best.source}"
            trace["discarded"] = [f.source for f in fragments[1:]]
            final_text = best.text.strip()
            trace["value"] = self._classify_and_register_value(
                final_text, input_text, intent, strategy, user_id,
            )
            return final_text, trace

        # --- 4. Eliminar duplicados textuales exactos ---
        seen_hashes = set()
        deduped: List[ResponseFragment] = []
        for f in fragments:
            h = f.text_hash
            if h not in seen_hashes:
                seen_hashes.add(h)
                deduped.append(f)
            else:
                trace["duplicates_removed"] += 1

        # --- 5. Eliminar duplicados semánticos ---
        unique = self._remove_semantic_duplicates(deduped)
        trace["duplicates_removed"] += len(deduped) - len(unique)

        if len(unique) == 1:
            trace["strategy"] = "dedup_single"
            final_text = unique[0].text.strip()
            trace["value"] = self._classify_and_register_value(
                final_text, input_text, intent, strategy, user_id,
            )
            return final_text, trace

        # --- 6. Fusionar fragmentos complementarios ---
        # Prioridad: el mejor fragmento primero, complementar con otros
        # solo si aportan información nueva sustancial.
        merged = self._merge_complementary(unique)
        trace["strategy"] = f"merged:{len(unique)}_fragments"
        trace["final_sources"] = [f.source for f in unique]

        # --- 7. Clasificación de valor + puente gravitacional ---
        final_text = merged.strip()
        trace["value"] = self._classify_and_register_value(
            final_text, input_text, intent, strategy, user_id,
        )

        return final_text, trace

    # -- Consolidar respuesta simple (shortcut) ---

    def consolidate_single(
        self,
        text: str,
        source: str,
    ) -> Tuple[str, Dict[str, Any]]:
        """Shortcut: consolida una sola respuesta (limpieza + dedup interno)."""
        if not text or not text.strip():
            return "", {"strategy": "empty"}

        cleaned = self._clean_internal_duplicates(text)
        trace = {"strategy": "single_cleaned", "source": source}
        return cleaned.strip(), trace

    # =====================================================================
    # Duplicate detection
    # =====================================================================

    def _remove_semantic_duplicates(
        self,
        fragments: List[ResponseFragment],
    ) -> List[ResponseFragment]:
        """
        Remove fragments that are semantically equivalent.

        Two fragments are considered duplicates if they share
        >70% of their significant words.
        """
        if len(fragments) <= 1:
            return fragments

        unique: List[ResponseFragment] = [fragments[0]]
        for candidate in fragments[1:]:
            is_dup = False
            for existing in unique:
                overlap = self._word_overlap(existing.text, candidate.text)
                if overlap >= self.SEMANTIC_OVERLAP_THRESHOLD:
                    is_dup = True
                    # Keep the one with higher priority (lower number)
                    if candidate.priority < existing.priority:
                        unique.remove(existing)
                        unique.append(candidate)
                    break
            if not is_dup:
                unique.append(candidate)

        return unique

    @staticmethod
    def _word_overlap(text_a: str, text_b: str) -> float:
        """Compute word overlap ratio between two texts."""
        words_a = set(re.findall(r"\b\w{3,}\b", text_a.lower()))
        words_b = set(re.findall(r"\b\w{3,}\b", text_b.lower()))
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        smaller = min(len(words_a), len(words_b))
        return len(intersection) / smaller if smaller > 0 else 0.0

    def _clean_internal_duplicates(self, text: str) -> str:
        """
        Clean duplicated paragraphs/sentences within a single text.

        Handles the case where the LLM or pipeline produces the same
        information block twice in one response.
        """
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if len(paragraphs) <= 1:
            return text

        # Deduplicate paragraphs by content hash
        seen = set()
        unique_paras: List[str] = []
        for p in paragraphs:
            normalized = re.sub(r"\s+", " ", p.lower())
            h = hashlib.md5(normalized.encode()).hexdigest()[:12]
            if h not in seen:
                seen.add(h)
                unique_paras.append(p)

        # Also check sentence-level duplication within paragraphs
        if len(unique_paras) > 1:
            unique_paras = self._dedup_sentences_across_paragraphs(unique_paras)

        return "\n\n".join(unique_paras)

    def _dedup_sentences_across_paragraphs(
        self,
        paragraphs: List[str],
    ) -> List[str]:
        """Remove sentences that appear in earlier paragraphs."""
        seen_sentences: set = set()
        result: List[str] = []

        for para in paragraphs:
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", para) if s.strip()]
            unique_sents: List[str] = []
            for s in sentences:
                key = re.sub(r"\s+", " ", s.lower())[:80]
                if key not in seen_sentences:
                    seen_sentences.add(key)
                    unique_sents.append(s)

            if unique_sents:
                result.append(" ".join(unique_sents))

        return result

    # =====================================================================
    # Merge complementary fragments
    # =====================================================================

    def _merge_complementary(
        self,
        fragments: List[ResponseFragment],
    ) -> str:
        """
        Merge multiple non-duplicate fragments into a single response.

        Strategy: use the best (highest priority) as base, then append
        only NEW information from lower-priority fragments.
        """
        if not fragments:
            return ""
        if len(fragments) == 1:
            return fragments[0].text

        # Base: highest priority fragment
        base = fragments[0]
        base_text = base.text.strip()
        base_words = set(re.findall(r"\b\w{3,}\b", base_text.lower()))

        additions: List[str] = []
        for frag in fragments[1:]:
            # Extract only NEW sentences from this fragment
            frag_sentences = [
                s.strip()
                for s in re.split(r"(?<=[.!?])\s+", frag.text)
                if s.strip()
            ]
            new_sentences: List[str] = []
            for s in frag_sentences:
                s_words = set(re.findall(r"\b\w{3,}\b", s.lower()))
                # Only add if it brings >40% new words
                if s_words:
                    overlap = len(s_words & base_words) / len(s_words)
                    if overlap < 0.60:
                        new_sentences.append(s)
                        base_words.update(s_words)

            if new_sentences:
                additions.append(" ".join(new_sentences))

        # Combine base + new information
        parts = [base_text]
        if additions:
            parts.extend(additions)

        merged = "\n\n".join(parts)

        # Truncate if too long
        if len(merged) > self.MAX_FINAL_LENGTH:
            merged = merged[:self.MAX_FINAL_LENGTH]
            # Cut at last sentence boundary
            last_end = max(merged.rfind("."), merged.rfind("!"), merged.rfind("?"))
            if last_end > len(merged) * 0.5:
                merged = merged[:last_end + 1]

        return merged


    # =====================================================================
    # Value classification + gravitational bridge
    # =====================================================================

    @staticmethod
    def _classify_and_register_value(
        final_text: str,
        input_text: str,
        intent: str,
        strategy: str,
        user_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Clasifica el valor del output y registra en el grafo gravitacional.

        Fail-safe: si el value_classifier no está disponible, retorna None.
        """
        if not input_text:
            return None

        try:
            from core.value_classifier import (
                get_value_classifier,
                register_value_in_graph,
            )

            vc = get_value_classifier()
            pkg = vc.classify(
                text=input_text,
                intent=intent,
                strategy=strategy,
                user_id=user_id,
            )

            # Registrar en grafo gravitacional (solo si tiene valor almacenable)
            node_id = register_value_in_graph(pkg, final_text, user_id)

            result = pkg.to_dict()
            if node_id:
                result["graph_node_id"] = node_id
            return result

        except Exception as exc:
            logger.debug("Value classification failed (non-critical): %s", exc)
            return None


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_consolidator: Optional[ResponseConsolidator] = None


def get_consolidator() -> ResponseConsolidator:
    global _consolidator
    if _consolidator is None:
        _consolidator = ResponseConsolidator()
    return _consolidator
