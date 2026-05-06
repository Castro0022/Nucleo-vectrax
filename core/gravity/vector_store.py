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

CREATE TABLE IF NOT EXISTS context_identities (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    identity_key        TEXT NOT NULL,
    people_json         TEXT,
    location            TEXT,
    dominant_emotion    TEXT,
    first_seen          TEXT,
    last_seen           TEXT,
    evidence_ids_json   TEXT,
    evidence_count      INTEGER DEFAULT 0,
    identity_mass       REAL DEFAULT 0,
    summary             TEXT,
    created_at          TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_context_identity_user
    ON context_identities(user_id);
CREATE INDEX IF NOT EXISTS idx_context_identity_key
    ON context_identities(user_id, identity_key);

CREATE TABLE IF NOT EXISTS essential_memories (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    type                TEXT,
    summary             TEXT,
    evidence_ids_json   TEXT,
    confidence          REAL,
    gravity_score       REAL DEFAULT 1.0,
    created_at          TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_essential_user
    ON essential_memories(user_id);
"""

# Columnas añadidas en migración no-destructiva (Memory Governance Engine).
# SQLite no soporta ADD COLUMN IF NOT EXISTS; usamos try/except por columna.
_DEEP_MEMORY_MIGRATIONS = [
    ("gravity_score",     "REAL DEFAULT 1.0"),
    ("retrieval_count",   "INTEGER DEFAULT 0"),
    ("last_retrieved_at", "TEXT"),
    ("fused_into",        "TEXT"),
    ("memory_status",     "TEXT DEFAULT 'ACTIVE'"),
    ("memory_type",       "TEXT DEFAULT 'episode'"),
]


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
        self._migrate_deep_memory_columns()
        self._lock = threading.Lock()

    # ----------------------------------------------------------- migration
    def _migrate_deep_memory_columns(self) -> None:
        """Aplica migraciones no-destructivas a deep_memory.

        SQLite ADD COLUMN no soporta IF NOT EXISTS; iteramos columna por
        columna y catcheamos el error si ya existe. Idempotente.
        """
        for col_name, col_def in _DEEP_MEMORY_MIGRATIONS:
            try:
                self.conn.execute(
                    f"ALTER TABLE deep_memory ADD COLUMN {col_name} {col_def}"
                )
                self.conn.commit()
            except sqlite3.OperationalError as exc:
                # "duplicate column name" => ya migrado, OK.
                if "duplicate column" not in str(exc).lower():
                    logger.debug("migrate %s skipped: %s", col_name, exc)

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
        """Test-only: drop all rows en todas las tablas del store."""
        with self._lock:
            self.conn.execute("DELETE FROM deep_memory")
            self.conn.execute("DELETE FROM context_identities")
            self.conn.execute("DELETE FROM essential_memories")
            self.conn.commit()

    # =========================================================================
    # Memory Governance Engine — helpers de columnas nuevas
    # =========================================================================

    def get_record(self, record_id: str) -> Optional[Dict[str, Any]]:
        """Lee un record completo (incluye columnas de governance)."""
        if not record_id:
            return None
        cur = self.conn.execute(
            "SELECT id, user_id, raw_text, summary, embedding_json, "
            "       tags_json, mass, ts, gravity_score, retrieval_count, "
            "       last_retrieved_at, fused_into, memory_status, memory_type "
            "FROM deep_memory WHERE id = ?",
            (record_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def list_active_for_user(
        self,
        user_id: str,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Lista los records ACTIVE de un user (no fusionados, no archivados)."""
        if not user_id:
            return []
        cur = self.conn.execute(
            "SELECT id, user_id, raw_text, summary, embedding_json, "
            "       tags_json, mass, ts, gravity_score, retrieval_count, "
            "       last_retrieved_at, fused_into, memory_status, memory_type "
            "FROM deep_memory "
            "WHERE user_id = ? AND COALESCE(memory_status, 'ACTIVE') = 'ACTIVE' "
            "ORDER BY ts DESC LIMIT ?",
            (user_id, int(limit)),
        )
        return [self._row_to_dict(r) for r in cur.fetchall()]

    def list_all_for_user(
        self,
        user_id: str,
        limit: int = 5000,
    ) -> List[Dict[str, Any]]:
        """Lista TODOS los records de un user (incluye fusionados/archived)."""
        if not user_id:
            return []
        cur = self.conn.execute(
            "SELECT id, user_id, raw_text, summary, embedding_json, "
            "       tags_json, mass, ts, gravity_score, retrieval_count, "
            "       last_retrieved_at, fused_into, memory_status, memory_type "
            "FROM deep_memory "
            "WHERE user_id = ? "
            "ORDER BY ts DESC LIMIT ?",
            (user_id, int(limit)),
        )
        return [self._row_to_dict(r) for r in cur.fetchall()]

    def touch_retrieval(self, record_id: str, now_iso: Optional[str] = None) -> None:
        """Incrementa retrieval_count y actualiza last_retrieved_at."""
        if not record_id:
            return
        ts = now_iso or _utc_iso()
        with self._lock:
            self.conn.execute(
                "UPDATE deep_memory "
                "SET retrieval_count = COALESCE(retrieval_count, 0) + 1, "
                "    last_retrieved_at = ? "
                "WHERE id = ?",
                (ts, record_id),
            )
            self.conn.commit()

    def set_status(self, record_id: str, status: str) -> bool:
        """Marca el memory_status (ACTIVE / COLD / FUSED / ARCHIVED)."""
        if not record_id or not status:
            return False
        with self._lock:
            cur = self.conn.execute(
                "UPDATE deep_memory SET memory_status = ? WHERE id = ?",
                (status, record_id),
            )
            self.conn.commit()
            return cur.rowcount > 0

    def set_memory_type(self, record_id: str, memory_type: str) -> bool:
        """Marca el memory_type (episode / DEEP / PERSON_RECOGNIZED / ...)."""
        if not record_id or not memory_type:
            return False
        with self._lock:
            cur = self.conn.execute(
                "UPDATE deep_memory SET memory_type = ? WHERE id = ?",
                (memory_type, record_id),
            )
            self.conn.commit()
            return cur.rowcount > 0

    def set_fused_into(self, record_id: str, identity_id: str) -> bool:
        """Marca el record como fusionado en una context_identity. NO borra."""
        if not record_id or not identity_id:
            return False
        with self._lock:
            cur = self.conn.execute(
                "UPDATE deep_memory "
                "SET fused_into = ?, memory_status = 'FUSED' "
                "WHERE id = ?",
                (identity_id, record_id),
            )
            self.conn.commit()
            return cur.rowcount > 0

    def set_gravity_score(self, record_id: str, score: float) -> bool:
        if not record_id:
            return False
        with self._lock:
            cur = self.conn.execute(
                "UPDATE deep_memory SET gravity_score = ? WHERE id = ?",
                (float(score), record_id),
            )
            self.conn.commit()
            return cur.rowcount > 0

    def add_gravity_delta(self, record_id: str, delta: float) -> float:
        """Aplica un delta a gravity_score (positivo o negativo). Devuelve total."""
        if not record_id:
            return 0.0
        with self._lock:
            self.conn.execute(
                "UPDATE deep_memory "
                "SET gravity_score = MAX(0, COALESCE(gravity_score, 1.0) + ?) "
                "WHERE id = ?",
                (float(delta), record_id),
            )
            self.conn.commit()
            cur = self.conn.execute(
                "SELECT gravity_score FROM deep_memory WHERE id = ?",
                (record_id,),
            )
            row = cur.fetchone()
            return float(row[0]) if row else 0.0

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        try:
            tags = json.loads(row[5]) if row[5] else []
        except Exception:
            tags = []
        try:
            embedding = json.loads(row[4]) if row[4] else []
        except Exception:
            embedding = []
        return {
            "id": row[0],
            "user_id": row[1],
            "raw_text": row[2],
            "summary": row[3] or "",
            "embedding": embedding,
            "tags": tags,
            "mass": float(row[6] or 0.0),
            "ts": float(row[7] or 0.0),
            "gravity_score": float(row[8] or 0.0),
            "retrieval_count": int(row[9] or 0),
            "last_retrieved_at": row[10] or "",
            "fused_into": row[11] or "",
            "memory_status": row[12] or "ACTIVE",
            "memory_type": row[13] or "episode",
        }

    # ====================================================== context_identities

    def upsert_context_identity(self, identity: Dict[str, Any]) -> str:
        """Crea o actualiza una context_identity. Devuelve el id."""
        if not identity.get("user_id") or not identity.get("identity_key"):
            raise ValueError("user_id and identity_key are required")
        ident_id = identity.get("id") or uuid.uuid4().hex[:16]
        now = _utc_iso()
        with self._lock:
            self.conn.execute(
                "INSERT INTO context_identities ("
                "  id, user_id, identity_key, people_json, location, "
                "  dominant_emotion, first_seen, last_seen, "
                "  evidence_ids_json, evidence_count, identity_mass, "
                "  summary, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "  people_json=excluded.people_json, "
                "  location=excluded.location, "
                "  dominant_emotion=excluded.dominant_emotion, "
                "  last_seen=excluded.last_seen, "
                "  evidence_ids_json=excluded.evidence_ids_json, "
                "  evidence_count=excluded.evidence_count, "
                "  identity_mass=excluded.identity_mass, "
                "  summary=excluded.summary, "
                "  updated_at=excluded.updated_at",
                (
                    ident_id,
                    identity["user_id"],
                    identity["identity_key"],
                    json.dumps(identity.get("people") or [],
                               ensure_ascii=False),
                    identity.get("location") or "",
                    identity.get("dominant_emotion") or "",
                    identity.get("first_seen") or now,
                    identity.get("last_seen") or now,
                    json.dumps(identity.get("evidence_ids") or [],
                               ensure_ascii=False),
                    int(identity.get("evidence_count") or 0),
                    float(identity.get("identity_mass") or 0.0),
                    identity.get("summary") or "",
                    identity.get("created_at") or now,
                    now,
                ),
            )
            self.conn.commit()
        return ident_id

    def get_context_identity_by_key(
        self, user_id: str, identity_key: str,
    ) -> Optional[Dict[str, Any]]:
        if not user_id or not identity_key:
            return None
        cur = self.conn.execute(
            "SELECT id, user_id, identity_key, people_json, location, "
            "       dominant_emotion, first_seen, last_seen, "
            "       evidence_ids_json, evidence_count, identity_mass, "
            "       summary, created_at, updated_at "
            "FROM context_identities "
            "WHERE user_id = ? AND identity_key = ?",
            (user_id, identity_key),
        )
        row = cur.fetchone()
        if not row:
            return None
        try:
            people = json.loads(row[3]) if row[3] else []
        except Exception:
            people = []
        try:
            ev_ids = json.loads(row[8]) if row[8] else []
        except Exception:
            ev_ids = []
        return {
            "id": row[0],
            "user_id": row[1],
            "identity_key": row[2],
            "people": people,
            "location": row[4] or "",
            "dominant_emotion": row[5] or "",
            "first_seen": row[6] or "",
            "last_seen": row[7] or "",
            "evidence_ids": ev_ids,
            "evidence_count": int(row[9] or 0),
            "identity_mass": float(row[10] or 0.0),
            "summary": row[11] or "",
            "created_at": row[12] or "",
            "updated_at": row[13] or "",
        }

    def list_context_identities(self, user_id: str) -> List[Dict[str, Any]]:
        if not user_id:
            return []
        cur = self.conn.execute(
            "SELECT identity_key FROM context_identities WHERE user_id = ?",
            (user_id,),
        )
        out = []
        for (key,) in cur.fetchall():
            ident = self.get_context_identity_by_key(user_id, key)
            if ident:
                out.append(ident)
        return out

    # ======================================================= essential_memories

    def upsert_essential_memory(self, ess: Dict[str, Any]) -> str:
        if not ess.get("user_id") or not ess.get("summary"):
            raise ValueError("user_id and summary are required")
        ess_id = ess.get("id") or uuid.uuid4().hex[:16]
        now = _utc_iso()
        with self._lock:
            self.conn.execute(
                "INSERT INTO essential_memories ("
                "  id, user_id, type, summary, evidence_ids_json, "
                "  confidence, gravity_score, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "  type=excluded.type, "
                "  summary=excluded.summary, "
                "  evidence_ids_json=excluded.evidence_ids_json, "
                "  confidence=excluded.confidence, "
                "  gravity_score=excluded.gravity_score, "
                "  updated_at=excluded.updated_at",
                (
                    ess_id,
                    ess["user_id"],
                    ess.get("type") or "principle",
                    ess["summary"],
                    json.dumps(ess.get("evidence_ids") or [],
                               ensure_ascii=False),
                    float(ess.get("confidence") or 0.0),
                    float(ess.get("gravity_score") or 1.0),
                    ess.get("created_at") or now,
                    now,
                ),
            )
            self.conn.commit()
        return ess_id

    def list_essential_memories(self, user_id: str) -> List[Dict[str, Any]]:
        if not user_id:
            return []
        cur = self.conn.execute(
            "SELECT id, user_id, type, summary, evidence_ids_json, "
            "       confidence, gravity_score, created_at, updated_at "
            "FROM essential_memories WHERE user_id = ? "
            "ORDER BY gravity_score DESC, updated_at DESC",
            (user_id,),
        )
        out = []
        for row in cur.fetchall():
            try:
                ev_ids = json.loads(row[4]) if row[4] else []
            except Exception:
                ev_ids = []
            out.append({
                "id": row[0],
                "user_id": row[1],
                "type": row[2] or "principle",
                "summary": row[3] or "",
                "evidence_ids": ev_ids,
                "confidence": float(row[5] or 0.0),
                "gravity_score": float(row[6] or 0.0),
                "created_at": row[7] or "",
                "updated_at": row[8] or "",
            })
        return out


# ===========================================================================
# Time helper
# ===========================================================================

def _utc_iso() -> str:
    """Devuelve un timestamp ISO-8601 en UTC para columnas TEXT."""
    import datetime
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
