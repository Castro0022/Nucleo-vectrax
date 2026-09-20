"""
Vectrax Conversation Ledger — Historial Conversacional Canónico
==================================================================
Corrección estructural 2026-09-20: `vectrax/user_memory.py` conservaba
como máximo 50 interacciones por usuario, usaba solo 8 en el contexto de
prompt, y truncaba a 1.500 caracteres. Ese mecanismo era razonable como
VENTANA DE PROMPT, pero nunca debió ser la política de conservación del
historial — perder interacciones #1..#(N-50) hace imposible responder
"¿qué hablamos el 12 de septiembre?" o "¿qué decidimos sobre X?" para
cualquier usuario con más de 50 turnos de conversación.

Este módulo es la FUENTE CANÓNICA, append-only, de todo turno de
conversación (usuario Y asistente) para CUALQUIER usuario, tenant, canal
o fecha — genérico, sin nombres/IDs/relaciones hardcodeados. Perfil,
hechos, core_memory, estrellas, resúmenes y "contexto reciente" son
PROYECCIONES DERIVADAS de este ledger, nunca una segunda fuente de verdad
paralela.

Principios (ver PARTE 1 del contrato):
  1. El mensaje entrante se registra INMEDIATAMENTE, antes de cualquier
     clasificación/LLM/búsqueda que pueda fallar.
  2. La respuesta se registra DESPUÉS de conocer el contenido final.
  3. Si el procesamiento falla, el mensaje del usuario permanece
     registrado con su estado de error (nunca se pierde ni se reescribe).
  4. Reintentos/duplicados son idempotentes vía una clave única estable.
  5. Sin límite de retención automática — NUNCA se borra por conteo.
  6. Aislamiento SIEMPRE por (tenant_id, user_id); el canal es
     procedencia, no identidad suficiente.

Interfaz de repositorio (`ConversationLedgerRepository`) para poder
sustituir SQLite en el futuro sin cambiar los llamadores. Una sola
implementación real hoy (`SQLiteConversationLedger`) — no se introduce
una segunda copia "temporal" que pueda divergir.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.memory.conversation_ledger")

SCHEMA_VERSION = 1

# Misma vault que el resto de la memoria de usuario (vault/user_memory.db) —
# un solo archivo, no una base "temporal" separada que pueda divergir.
_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "vault", "user_memory.db",
)

_DEFAULT_TENANT = "default"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS conversation_events (
    event_id          TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL DEFAULT 'default',
    user_id           TEXT NOT NULL,
    channel           TEXT NOT NULL DEFAULT '',
    chat_id           TEXT NOT NULL DEFAULT '',
    session_id        TEXT NOT NULL DEFAULT '',
    message_id        TEXT NOT NULL DEFAULT '',
    correlation_id    TEXT NOT NULL DEFAULT '',
    role              TEXT NOT NULL,
    content           TEXT NOT NULL DEFAULT '',
    timestamp         REAL NOT NULL,
    status            TEXT NOT NULL DEFAULT 'received',
    error             TEXT NOT NULL DEFAULT '',
    reply_to_event_id TEXT NOT NULL DEFAULT '',
    source            TEXT NOT NULL DEFAULT '',
    schema_version    INTEGER NOT NULL DEFAULT 1,
    idempotency_key   TEXT NOT NULL DEFAULT '',
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_conv_events_idem
    ON conversation_events(idempotency_key) WHERE idempotency_key != '';
CREATE INDEX IF NOT EXISTS idx_conv_events_tenant_user_ts
    ON conversation_events(tenant_id, user_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_conv_events_correlation
    ON conversation_events(correlation_id);
CREATE INDEX IF NOT EXISTS idx_conv_events_message
    ON conversation_events(tenant_id, user_id, message_id);
"""

_VALID_ROLES = ("user", "assistant")
_VALID_STATUSES = ("received", "processing", "delivered", "error", "ignored")


# ---------------------------------------------------------------------------
# Modelo de datos
# ---------------------------------------------------------------------------

@dataclass
class ConversationEvent:
    """Un turno de conversación (usuario o asistente) — nunca se borra."""
    user_id: str
    role: str                       # "user" | "assistant"
    content: str = ""
    tenant_id: str = _DEFAULT_TENANT
    channel: str = ""
    chat_id: str = ""
    session_id: str = ""
    message_id: str = ""
    correlation_id: str = ""
    status: str = "received"
    error: str = ""
    reply_to_event_id: str = ""
    source: str = ""
    schema_version: int = SCHEMA_VERSION
    idempotency_key: str = ""
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:24])
    timestamp: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if self.role not in _VALID_ROLES:
            raise ValueError(f"role inválido: {self.role!r} (debe ser 'user'|'assistant')")
        if not self.tenant_id:
            self.tenant_id = _DEFAULT_TENANT
        if not self.idempotency_key:
            self.idempotency_key = compute_idempotency_key(
                tenant_id=self.tenant_id, user_id=self.user_id, channel=self.channel,
                message_id=self.message_id, correlation_id=self.correlation_id,
                role=self.role, content=self.content, timestamp=self.timestamp,
            )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def compute_idempotency_key(
    *, tenant_id: str, user_id: str, channel: str, message_id: str,
    correlation_id: str, role: str, content: str, timestamp: float,
) -> str:
    """Clave única estable para deduplicar reintentos/duplicados.

    Prioridad (más precisa primero):
      1. tenant+user+channel+message_id+role -- el identificador MÁS
         estable cuando el transporte (Telegram, etc.) provee message_id.
      2. tenant+user+correlation_id+role -- cuando no hay message_id pero
         sí un correlation_id de la request.
      3. Fallback: hash de tenant+user+role+content+bucket-de-tiempo
         (ventana de 2s) -- para callers legacy sin message_id ni
         correlation_id; suficiente para colapsar reintentos inmediatos
         del MISMO contenido sin fusionar mensajes genuinamente distintos.
    """
    if message_id:
        raw = f"{tenant_id}:{user_id}:{channel}:{message_id}:{role}"
    elif correlation_id:
        raw = f"{tenant_id}:{user_id}:{correlation_id}:{role}"
    else:
        bucket = int(timestamp // 2)
        content_hash = hashlib.sha256((content or "").encode("utf-8")).hexdigest()[:24]
        raw = f"{tenant_id}:{user_id}:{role}:{content_hash}:{bucket}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Interfaz de repositorio -- permite sustituir SQLite sin tocar llamadores
# ---------------------------------------------------------------------------

class ConversationLedgerRepository(ABC):
    """Contrato del ledger conversacional. Una sola implementación real hoy
    (`SQLiteConversationLedger`); esta interfaz existe para poder sustituir
    el almacenamiento en el futuro (p.ej. Postgres/DynamoDB a escala) sin
    reescribir los llamadores."""

    @abstractmethod
    def append(self, event: ConversationEvent) -> str:
        """Inserta el evento. Idempotente: si `idempotency_key` ya existe,
        devuelve el `event_id` EXISTENTE sin crear un duplicado."""

    @abstractmethod
    def mark_status(self, event_id: str, status: str, error: str = "") -> None:
        """Actualiza el estado de un evento ya registrado (p.ej. tras un
        fallo de procesamiento)."""

    @abstractmethod
    def get_by_id(self, event_id: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_by_correlation(self, correlation_id: str) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_recent(
        self, tenant_id: str, user_id: str, limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Últimos N eventos (orden cronológico ascendente) -- SOLO limita
        cuántos se DEVUELVEN a esta llamada, nunca borra nada."""

    @abstractmethod
    def search(
        self,
        tenant_id: str,
        user_id: str,
        *,
        since: Optional[float] = None,
        until: Optional[float] = None,
        keyword: str = "",
        role: str = "",
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Búsqueda combinable por rango temporal + keyword + rol."""

    @abstractmethod
    def count(self, tenant_id: str, user_id: str) -> int:
        ...

    @abstractmethod
    def count_total(self) -> int:
        ...


# ---------------------------------------------------------------------------
# Implementación SQLite
# ---------------------------------------------------------------------------

class SQLiteConversationLedger(ConversationLedgerRepository):
    def __init__(self, db_path: str = "") -> None:
        self._db_path = db_path or _DB_PATH
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        conn = self._conn()
        try:
            conn.executescript(_CREATE_SQL)
            conn.commit()
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def append(self, event: ConversationEvent) -> str:
        with self._lock:
            conn = self._conn()
            try:
                existing = conn.execute(
                    "SELECT event_id FROM conversation_events WHERE idempotency_key = ?",
                    (event.idempotency_key,),
                ).fetchone()
                if existing:
                    logger.debug(
                        "conversation_ledger: idempotent hit, reusing event_id=%s",
                        existing["event_id"],
                    )
                    return existing["event_id"]

                conn.execute(
                    """INSERT INTO conversation_events (
                        event_id, tenant_id, user_id, channel, chat_id, session_id,
                        message_id, correlation_id, role, content, timestamp,
                        status, error, reply_to_event_id, source, schema_version,
                        idempotency_key, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?, ?,?,?)""",
                    (
                        event.event_id, event.tenant_id, event.user_id, event.channel,
                        event.chat_id, event.session_id, event.message_id,
                        event.correlation_id, event.role, event.content, event.timestamp,
                        event.status, event.error, event.reply_to_event_id, event.source,
                        event.schema_version, event.idempotency_key,
                        event.created_at, event.updated_at,
                    ),
                )
                conn.commit()
                return event.event_id
            except sqlite3.IntegrityError:
                # Carrera: otro hilo/proceso insertó el mismo idempotency_key
                # entre el SELECT y el INSERT -- recuperar el existente.
                row = conn.execute(
                    "SELECT event_id FROM conversation_events WHERE idempotency_key = ?",
                    (event.idempotency_key,),
                ).fetchone()
                return row["event_id"] if row else event.event_id
            finally:
                conn.close()

    def mark_status(self, event_id: str, status: str, error: str = "") -> None:
        if status not in _VALID_STATUSES:
            status = "error"
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE conversation_events SET status = ?, error = ?, updated_at = ? "
                    "WHERE event_id = ?",
                    (status, error[:500], time.time(), event_id),
                )
                conn.commit()
            finally:
                conn.close()

    def get_by_id(self, event_id: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM conversation_events WHERE event_id = ?", (event_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_by_correlation(self, correlation_id: str) -> List[Dict[str, Any]]:
        if not correlation_id:
            return []
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM conversation_events WHERE correlation_id = ? "
                "ORDER BY timestamp ASC",
                (correlation_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_recent(
        self, tenant_id: str, user_id: str, limit: int = 50,
    ) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM conversation_events WHERE tenant_id = ? AND user_id = ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (tenant_id or _DEFAULT_TENANT, user_id, limit),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]
        finally:
            conn.close()

    def search(
        self,
        tenant_id: str,
        user_id: str,
        *,
        since: Optional[float] = None,
        until: Optional[float] = None,
        keyword: str = "",
        role: str = "",
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        clauses = ["tenant_id = ?", "user_id = ?"]
        params: List[Any] = [tenant_id or _DEFAULT_TENANT, user_id]
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since)
        if until is not None:
            clauses.append("timestamp <= ?")
            params.append(until)
        if role:
            clauses.append("role = ?")
            params.append(role)
        if keyword:
            clauses.append("content LIKE ?")
            params.append(f"%{keyword}%")
        params.append(limit)
        sql = (
            "SELECT * FROM conversation_events WHERE " + " AND ".join(clauses)
            + " ORDER BY timestamp DESC LIMIT ?"
        )
        conn = self._conn()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in reversed(rows)]
        finally:
            conn.close()

    def count(self, tenant_id: str, user_id: str) -> int:
        conn = self._conn()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM conversation_events WHERE tenant_id = ? AND user_id = ?",
                (tenant_id or _DEFAULT_TENANT, user_id),
            ).fetchone()[0]
        finally:
            conn.close()

    def count_total(self) -> int:
        conn = self._conn()
        try:
            return conn.execute("SELECT COUNT(*) FROM conversation_events").fetchone()[0]
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Singleton + API de conveniencia (usada por los adaptadores de transporte)
# ---------------------------------------------------------------------------

_repo: Optional[ConversationLedgerRepository] = None
_repo_lock = threading.Lock()


def get_conversation_ledger() -> ConversationLedgerRepository:
    global _repo
    if _repo is None:
        with _repo_lock:
            if _repo is None:
                _repo = SQLiteConversationLedger()
    return _repo


def set_conversation_ledger(repo: ConversationLedgerRepository) -> None:
    """Inyección explícita -- usada por tests para aislar la DB, y por una
    futura migración de backend sin tocar los llamadores."""
    global _repo
    _repo = repo


def record_user_message(
    *,
    user_id: str,
    content: str,
    tenant_id: str = _DEFAULT_TENANT,
    channel: str = "",
    chat_id: str = "",
    session_id: str = "",
    message_id: str = "",
    correlation_id: str = "",
    source: str = "",
) -> str:
    """Registra el mensaje entrante del usuario -- debe llamarse ANTES de
    cualquier clasificación/LLM/búsqueda que pueda fallar (Regla 1)."""
    event = ConversationEvent(
        user_id=user_id, role="user", content=content or "", tenant_id=tenant_id,
        channel=channel, chat_id=str(chat_id or ""), session_id=session_id,
        message_id=str(message_id or ""), correlation_id=str(correlation_id or ""),
        status="received", source=source,
    )
    try:
        return get_conversation_ledger().append(event)
    except Exception as exc:
        logger.error("conversation_ledger: failed to record user message: %s", exc)
        return ""


def record_assistant_message(
    *,
    user_id: str,
    content: str,
    tenant_id: str = _DEFAULT_TENANT,
    channel: str = "",
    chat_id: str = "",
    session_id: str = "",
    message_id: str = "",
    correlation_id: str = "",
    reply_to_event_id: str = "",
    status: str = "delivered",
    error: str = "",
    source: str = "",
) -> str:
    """Registra la respuesta del asistente -- DESPUÉS de conocer el
    contenido final enviado (Regla 2)."""
    event = ConversationEvent(
        user_id=user_id, role="assistant", content=content or "", tenant_id=tenant_id,
        channel=channel, chat_id=str(chat_id or ""), session_id=session_id,
        message_id=str(message_id or ""), correlation_id=str(correlation_id or ""),
        reply_to_event_id=reply_to_event_id, status=status, error=error, source=source,
    )
    try:
        return get_conversation_ledger().append(event)
    except Exception as exc:
        logger.error("conversation_ledger: failed to record assistant message: %s", exc)
        return ""


def mark_event_error(event_id: str, error: str) -> None:
    """Si el procesamiento falla tras registrar el mensaje del usuario, su
    evento permanece pero se marca con el estado de error (Regla 3) --
    nunca se elimina ni se reescribe el contenido original."""
    if not event_id:
        return
    try:
        get_conversation_ledger().mark_status(event_id, "error", error=error)
    except Exception as exc:
        logger.debug("conversation_ledger: mark_event_error failed: %s", exc)
