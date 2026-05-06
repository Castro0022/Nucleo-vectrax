"""
core/gravity/vector_store.py — SQLite-backed vector store.

Tabla `deep_memory`:
    id            TEXT PRIMARY KEY        record id (uuid)
    user_id       TEXT NOT NULL           dueño del registro (RBAC)
    raw_text      TEXT NOT NULL           texto original
    summary       TEXT                    resumen humano (opcional)
    embedding_json TEXT NOT NULL          embedding como JSON list[float]
    tags_json     TEXT                    tags como JSON list[str]
    mass          REAL NOT NULL DEFAULT 0 masa gravitacional acumulada
    ts            REAL NOT NULL           timestamp de creación

Índice: (user_id, ts DESC) — escala lineal en items por user, pero
queries son O(N_user) con cosine local. Suficiente para 10⁴-10⁵ items
por user; para escalas mayores conviene migrar a un vector DB nativo
(FAISS, pgvector, qdrant). El contrato de query() es estable: cualquier
backend futuro implementa la misma firma.

Diseño:
  - Cero red. Todo el cálculo es local con numpy.
  - Filtrado ESTRICTO por user_id antes de calcular similitudes:
    User_A NUNCA ve datos de User_B aunque las queries sean parecidas.
  - Thread-safe: lock global para escrituras; lecturas concurrentes OK.
  - Embeddings se guardan como JSON list[float] (legible, debuggeable).
    Para cargas grandes futuro: bytes + struct.pack.

API pública:
    SQLiteVectorStore(db_path=None)
    store.upsert(record: DeepMemoryRecord)
    store.query(user_id, query_embedding, limit=5) -> list[dict]
    store.delete(record_id)
    store.count_for_user(user_id) -> int
    store.add_mass(record_id, delta) -> float (nuevo total)
    store.close()

cosine_similarity(v1, v2) -> float disponible como utilidad pública.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger("vectrax.gravity.vector_store")


# ===========================================================================
# Cosine similarity
# ===========================================================================

def cosine_similarity(v1: Sequence[float], v2: Sequence[float]) -> float:
    """Devuelve el cosine de dos vectores. 0.0 si alguno es nulo.

    Implementación numpy (rápida, evita división por cero).
    """
    a = np.asarray(v1, dtype=np.float64)
    b = np.asarray(v2, dtype=np.float64)
    if a.size == 0 or b.size == 0 or a.size != b.size:
        return 0.0
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# ===========================================================================
# Record
# ===========================================================================

@dataclass
class DeepMemoryRecord:
    """Estructura canónica de un item en deep_memory."""
    user_id: str
    raw_text: str
    embedding: List[float]
    summary: str = ""
    tags: List[str] = field(default_factory=list)
    mass: float = 0.0
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    ts: float = field(default_factory=time.time)

    def to_row(self) -> tuple:
        return (
            self.id,
            self.user_id,
            self.raw_text,
            self.summary or "",
            json.dumps(self.embedding, ensure_ascii=False),
            json.dumps(self.tags or [], ensure_ascii=False),
            float(self.mass),
            float(self.ts),
        )


# ===========================================================================
# Store
# ===========================================================================

_DEFAULT_DB_PATH = os.environ.get(
    "VECTRAX_GRAVITY_DB",
    os.path.join(
        os.path.expanduser("~"),
        ".vectrax", "gravity.db",
    ),
)

_CREATE = """
CREATE TABLE IF NOT EXISTS deep_memory (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL,
    raw_text       TEXT NOT NULL,
    summary        TEXT,
    embedding_json TEXT NOT NULL,
    tags_json      TEXT,
    mass           REAL NOT NULL DEFAULT 0,
    ts             REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dm_user_ts ON deep_memory(user_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_dm_user_mass ON deep_memory(user_id, mass DESC);
"""


class SQLiteVectorStore:
    """Vector store persistente sobre SQLite con cosine similarity local."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or _DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        # check_same_thread=False habilita uso desde el ThreadPoolExecutor
        # del worker; el lock interno serializa escrituras.
        self.conn = sqlite3.connect(self.db_path, timeout=5,
                                    check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_CREATE)
        self._lock = threading.Lock()

    # -------------------------------------------------------------- upsert
    def upsert(self, record: DeepMemoryRecord) -> str:
        """Inserta o reemplaza un record. Devuelve el id."""
        if not record.user_id:
            raise ValueError("user_id is required")
        if not record.raw_text:
            raise ValueError("raw_text is required")
        if not record.embedding:
            raise ValueError("embedding is required")
        with self._lock:
            self.conn.execute(
                "INSERT INTO deep_memory "
                "(id, user_id, raw_text, summary, embedding_json, "
                " tags_json, mass, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "  user_id=excluded.user_id, "
                "  raw_text=excluded.raw_text, "
                "  summary=excluded.summary, "
                "  embedding_json=excluded.embedding_json, "
                "  tags_json=excluded.tags_json, "
                "  mass=excluded.mass, "
                "  ts=excluded.ts",
                record.to_row(),
            )
            self.conn.commit()
        return record.id

    # --------------------------------------------------------------- query
    def query(
        self,
        user_id: str,
        query_embedding: Sequence[float],
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Devuelve los top_k records más similares al query_embedding.

        Pasos:
          1. Filtrado ESTRICTO por user_id en SQL (RBAC en la DB).
          2. Cosine similarity local con numpy contra cada embedding.
          3. Sort DESC por score, devuelve top_k.

        Si user_id está vacío o el embedding es inválido, devuelve [].
        """
        if not user_id or not query_embedding:
            return []
        try:
            cursor = self.conn.execute(
                "SELECT id, raw_text, summary, embedding_json, tags_json, "
                "       mass, ts "
                "FROM deep_memory WHERE user_id = ?",
                (user_id,),
            )
            rows = cursor.fetchall()
        except Exception as exc:
            logger.warning("vector_store query failed: %s", exc)
            return []

        if not rows:
            return []

        q = np.asarray(query_embedding, dtype=np.float64)
        results: List[Dict[str, Any]] = []
        for row in rows:
            record_id, text, summary, emb_json, tags_json, mass, ts = row
            try:
                db_emb = json.loads(emb_json)
            except Exception:
                continue
            score = cosine_similarity(q, db_emb)
            try:
                tags = json.loads(tags_json) if tags_json else []
            except Exception:
                tags = []
            results.append({
                "id": record_id,
                "text": text,
                "summary": summary or "",
                "tags": tags,
                "mass": float(mass or 0.0),
                "ts": float(ts or 0.0),
                "score": float(score),
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[: max(0, int(limit))]

    # -------------------------------------------------------------- delete
    def delete(self, record_id: str) -> bool:
        if not record_id:
            return False
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM deep_memory WHERE id = ?", (record_id,),
            )
            self.conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------- counts
    def count_for_user(self, user_id: str) -> int:
        if not user_id:
            return 0
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM deep_memory WHERE user_id = ?",
            (user_id,),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0

    # ---------------------------------------------------------------- mass
    def add_mass(self, record_id: str, delta: float) -> float:
        """Suma `delta` a la masa del record. Devuelve el total nuevo."""
        if not record_id:
            return 0.0
        with self._lock:
            self.conn.execute(
                "UPDATE deep_memory SET mass = COALESCE(mass, 0) + ? "
                "WHERE id = ?",
                (float(delta), record_id),
            )
            self.conn.commit()
            cur = self.conn.execute(
                "SELECT mass FROM deep_memory WHERE id = ?",
                (record_id,),
            )
            row = cur.fetchone()
            return float(row[0]) if row else 0.0

    # -------------------------------------------------------------- close
    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    # ----------------------------------------------------------- test util
    def reset_for_tests(self) -> None:
        """Test-only: drop all rows."""
        with self._lock:
            self.conn.execute("DELETE FROM deep_memory")
            self.conn.commit()
