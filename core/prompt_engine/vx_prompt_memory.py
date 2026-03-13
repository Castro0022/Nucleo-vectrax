"""
VX Prompt Engine — Memoria y Aprendizaje
==========================================
Persiste historial de prompts optimizados y aprende patrones exitosos.
Solo almacena categorías abstractas (intent, modelo, score), nunca texto
crudo del usuario ni datos personales.

Tabla: prompt_history en vault/vectrax_ledger.db
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DB_PATH = os.path.join("vault", "vectrax_ledger.db")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PromptRecord:
    """Registro abstracto de un prompt optimizado (sin texto literal)."""
    prompt_id: str
    intent: str
    model_used: str
    tone: str
    output_format: str
    language: str
    confidence: float
    result_score: Optional[float] = None
    execution_ms: Optional[int] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


@dataclass
class PatternStats:
    """Estadísticas agregadas de un patrón intent+modelo."""
    intent: str
    model: str
    total_uses: int
    avg_score: float
    best_score: float
    avg_confidence: float
    last_used: str


# ---------------------------------------------------------------------------
# PromptMemory
# ---------------------------------------------------------------------------

class PromptMemory:
    """
    Persistencia y aprendizaje estadístico para el VX Prompt Engine.

    Reglas de privacidad:
    - Solo almacena categorías abstractas (intent, modelo, tone, format)
    - Nunca guarda texto del usuario, prompts literales ni datos personales
    - Los scores son numéricos y no-reconstruibles
    """

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or DB_PATH
        self._init_db()

    # -- DB setup -----------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS prompt_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt_id TEXT NOT NULL UNIQUE,
                intent TEXT NOT NULL,
                model_used TEXT NOT NULL,
                tone TEXT DEFAULT '',
                output_format TEXT DEFAULT '',
                language TEXT DEFAULT 'es',
                confidence REAL DEFAULT 0.0,
                result_score REAL DEFAULT NULL,
                execution_ms INTEGER DEFAULT NULL,
                created_at TEXT NOT NULL,
                evaluated_at TEXT DEFAULT NULL
            )
        """)

        # Índices para consultas frecuentes
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ph_intent
            ON prompt_history(intent)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ph_intent_model
            ON prompt_history(intent, model_used)
        """)

        conn.commit()
        conn.close()
        logger.debug("prompt_history table ready")

    # -- Recording ----------------------------------------------------------

    def record(self, record: PromptRecord) -> None:
        """Registra un prompt optimizado (sin texto literal)."""
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT OR REPLACE INTO prompt_history
                    (prompt_id, intent, model_used, tone, output_format,
                     language, confidence, result_score, execution_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.prompt_id,
                record.intent,
                record.model_used,
                record.tone,
                record.output_format,
                record.language,
                record.confidence,
                record.result_score,
                record.execution_ms,
                record.created_at,
            ))
            conn.commit()
        finally:
            conn.close()

    def record_result(self, prompt_id: str, score: float, execution_ms: Optional[int] = None) -> bool:
        """
        Registra el resultado de una ejecución.

        Args:
            prompt_id: ID del prompt evaluado.
            score: Calidad del resultado (0.0 - 1.0).
            execution_ms: Tiempo de ejecución en milisegundos.

        Returns:
            True si el registro fue actualizado.
        """
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE prompt_history
                SET result_score = ?,
                    execution_ms = COALESCE(?, execution_ms),
                    evaluated_at = ?
                WHERE prompt_id = ?
            """, (score, execution_ms, datetime.now().isoformat(timespec="seconds"), prompt_id))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # -- Pattern queries ----------------------------------------------------

    def get_best_pattern(self, intent: str, min_uses: int = 2) -> Optional[PatternStats]:
        """
        Retorna el patrón (intent+modelo) con mejor score promedio.

        Solo considera combinaciones con al menos `min_uses` usos evaluados.
        """
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT
                    intent,
                    model_used,
                    COUNT(*) as total_uses,
                    AVG(result_score) as avg_score,
                    MAX(result_score) as best_score,
                    AVG(confidence) as avg_confidence,
                    MAX(created_at) as last_used
                FROM prompt_history
                WHERE intent = ?
                  AND result_score IS NOT NULL
                GROUP BY intent, model_used
                HAVING COUNT(*) >= ?
                ORDER BY avg_score DESC
                LIMIT 1
            """, (intent, min_uses))

            row = cur.fetchone()
            if not row:
                return None

            return PatternStats(
                intent=row["intent"],
                model=row["model_used"],
                total_uses=row["total_uses"],
                avg_score=round(row["avg_score"], 4),
                best_score=round(row["best_score"], 4),
                avg_confidence=round(row["avg_confidence"], 4),
                last_used=row["last_used"],
            )
        finally:
            conn.close()

    def get_patterns_for_intent(self, intent: str) -> List[PatternStats]:
        """Retorna todos los patrones evaluados para un intent, ordenados por score."""
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT
                    intent,
                    model_used,
                    COUNT(*) as total_uses,
                    AVG(result_score) as avg_score,
                    MAX(result_score) as best_score,
                    AVG(confidence) as avg_confidence,
                    MAX(created_at) as last_used
                FROM prompt_history
                WHERE intent = ?
                  AND result_score IS NOT NULL
                GROUP BY intent, model_used
                ORDER BY avg_score DESC
            """, (intent,))

            return [
                PatternStats(
                    intent=row["intent"],
                    model=row["model_used"],
                    total_uses=row["total_uses"],
                    avg_score=round(row["avg_score"], 4),
                    best_score=round(row["best_score"], 4),
                    avg_confidence=round(row["avg_confidence"], 4),
                    last_used=row["last_used"],
                )
                for row in cur.fetchall()
            ]
        finally:
            conn.close()

    def detect_repetition(self, intent: str, threshold: int = 5) -> Optional[Dict]:
        """
        Detecta si un intent se repite más de `threshold` veces.

        Retorna estadísticas y el mejor patrón conocido si se supera el umbral.
        Solo notifica cuando hay una solución lista (patrón con buen score).
        """
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT COUNT(*) as cnt
                FROM prompt_history
                WHERE intent = ?
            """, (intent,))
            row = cur.fetchone()
            count = row["cnt"] if row else 0

            if count < threshold:
                return None

            best = self.get_best_pattern(intent)
            if not best or best.avg_score < 0.6:
                return None

            return {
                "intent": intent,
                "repetition_count": count,
                "best_model": best.model,
                "avg_score": best.avg_score,
                "suggestion": f"Patrón repetido {count}x. Mejor modelo: {best.model} (score: {best.avg_score:.2f})",
            }
        finally:
            conn.close()

    def get_stats(self) -> Dict:
        """Estadísticas globales del historial (solo métricas agregadas)."""
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT
                    COUNT(*) as total,
                    COUNT(result_score) as evaluated,
                    AVG(CASE WHEN result_score IS NOT NULL THEN result_score END) as avg_score,
                    COUNT(DISTINCT intent) as unique_intents,
                    COUNT(DISTINCT model_used) as unique_models
                FROM prompt_history
            """)
            row = cur.fetchone()
            return {
                "total_prompts": row["total"],
                "evaluated": row["evaluated"],
                "avg_score": round(row["avg_score"], 4) if row["avg_score"] else None,
                "unique_intents": row["unique_intents"],
                "unique_models": row["unique_models"],
            }
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_global_memory: Optional[PromptMemory] = None


def get_prompt_memory() -> PromptMemory:
    """Retorna la instancia global de PromptMemory."""
    global _global_memory
    if _global_memory is None:
        _global_memory = PromptMemory()
    return _global_memory
