"""
Contextual Presence Layer
===========================
Vectrax observa. Solo interviene cuando detecta un momento significativo.
No es onboarding, no son notificaciones, no es engagement artificial.
Es presencia.

Principio:
  Observar primero. Interrumpir poco. Hablar solo con contexto.
  Silencio el resto del tiempo.

Triggers:
  1. Return After Silence — usuario regresa después de >2h
  2. Topic Convergence — 3+ consultas del mismo tema en 7 días

Límites duros:
  - 1 intervención por sesión (gaps <30 min = misma sesión)
  - Nunca en los primeros 15 segundos
  - Nunca repetir el mismo mensaje (hash persistido)
  - Nunca inventar temas (solo memoria real)
  - Nunca perseguir al usuario
  - Cooldown 24h después de intervenir
  - Solo usuarios con >5 interacciones

API:
  record_presence(user_id, content)     — cada mensaje entrante
  evaluate_presence(user_id) → str|None — retorna intervención o None

Creador: Mario Bravo Castro
Fecha: 2026-06-14
"""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.contextual_presence")

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "presence_state.db",
)
_USER_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "user_memory.db",
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SESSION_GAP = 1800          # 30 min gap = new session
SILENCE_THRESHOLD = 7200    # 2h = "return after silence"
FIRST_MSG_COOLDOWN = 15     # seconds before any intervention
COOLDOWN_HOURS = 24         # hours between interventions per user
MIN_INTERACTIONS = 5        # minimum interactions before activating
TOPIC_WINDOW_DAYS = 7       # days to look back for topic convergence
TOPIC_MIN_COUNT = 3         # minimum occurrences to trigger

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

_CREATE = """
CREATE TABLE IF NOT EXISTS presence_sessions (
    user_id       TEXT PRIMARY KEY,
    session_start REAL DEFAULT 0,
    last_message  REAL DEFAULT 0,
    message_count INTEGER DEFAULT 0,
    intervention  INTEGER DEFAULT 0,
    last_content  TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS presence_sent (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   TEXT NOT NULL,
    trigger   TEXT NOT NULL,
    message   TEXT NOT NULL,
    sent_at   REAL NOT NULL,
    msg_hash  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ps_hash ON presence_sent(msg_hash);
"""


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=3)
    conn.executescript(_CREATE)
    return conn


# ---------------------------------------------------------------------------
# Record presence — called on every incoming message
# ---------------------------------------------------------------------------

def record_presence(user_id: str, content: str) -> None:
    """Record user activity. Updates session state."""
    now = time.time()
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT session_start, last_message, message_count FROM presence_sessions WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        if row:
            session_start, last_message, msg_count = row
            gap = now - last_message if last_message else 0

            if gap > SESSION_GAP:
                # New session
                conn.execute(
                    "UPDATE presence_sessions SET session_start=?, last_message=?, message_count=1, intervention=0, last_content=? WHERE user_id=?",
                    (now, now, content[:200], user_id),
                )
            else:
                # Same session
                conn.execute(
                    "UPDATE presence_sessions SET last_message=?, message_count=message_count+1, last_content=? WHERE user_id=?",
                    (now, content[:200], user_id),
                )
        else:
            # First time
            conn.execute(
                "INSERT INTO presence_sessions (user_id, session_start, last_message, message_count, intervention, last_content) VALUES (?,?,?,1,0,?)",
                (user_id, now, now, content[:200]),
            )

        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("record_presence failed: %s", exc)


# ---------------------------------------------------------------------------
# Evaluate presence — returns intervention or None
# ---------------------------------------------------------------------------

def evaluate_presence(user_id: str) -> Optional[str]:
    """
    Check if a contextual intervention is appropriate.

    Returns intervention message or None (silence).
    NEVER raises — always returns cleanly.
    """
    try:
        # === LIMIT: minimum interactions ===
        interaction_count = _get_interaction_count(user_id)
        if interaction_count < MIN_INTERACTIONS:
            return None

        # === LIMIT: cooldown 24h ===
        if _cooldown_active(user_id):
            return None

        # === LIMIT: already intervened this session ===
        session = _get_session(user_id)
        if not session:
            return None
        if session["intervention"]:
            return None

        # === LIMIT: first 15 seconds ===
        elapsed = time.time() - session["session_start"]
        if elapsed < FIRST_MSG_COOLDOWN:
            return None

        # === TRIGGER 1: Return after silence ===
        trigger, context = _check_return_after_silence(user_id, session)
        if trigger:
            return _generate_and_record(user_id, "return_silence", context)

        # === TRIGGER 4: Topic convergence ===
        trigger, context = _check_topic_convergence(user_id)
        if trigger:
            return _generate_and_record(user_id, "topic_convergence", context)

        return None

    except Exception as exc:
        logger.debug("evaluate_presence failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------

def _check_return_after_silence(
    user_id: str, session: Dict,
) -> Tuple[bool, str]:
    """Trigger 1: user returns after >2h silence."""
    # Only trigger on the FIRST message of the session
    if session["message_count"] > 1:
        return False, ""

    # Check gap from previous session's last message
    try:
        conn = sqlite3.connect(_USER_DB, timeout=2)
        rows = conn.execute(
            "SELECT user_input, bot_output, timestamp FROM interactions "
            "WHERE user_id = ? ORDER BY timestamp DESC LIMIT 2",
            (user_id,),
        ).fetchall()
        conn.close()

        if len(rows) < 2:
            return False, ""

        # rows[0] = current message, rows[1] = previous
        prev_ts = rows[1][2]
        gap = time.time() - prev_ts

        if gap < SILENCE_THRESHOLD:
            return False, ""

        # Extract last topic from previous interaction
        last_input = rows[1][0] or ""
        last_output = rows[1][1] or ""
        if not last_input:
            return False, ""

        context = f"Regresó después de {int(gap / 3600)}h. Último tema: {last_input[:100]}"
        return True, context

    except Exception:
        return False, ""


def _check_topic_convergence(user_id: str) -> Tuple[bool, str]:
    """Trigger 4: user asked about same topic 3+ times in 7 days."""
    try:
        cutoff = time.time() - (TOPIC_WINDOW_DAYS * 86400)
        conn = sqlite3.connect(_USER_DB, timeout=2)
        rows = conn.execute(
            "SELECT user_input FROM interactions "
            "WHERE user_id = ? AND timestamp > ? ORDER BY timestamp DESC",
            (user_id, cutoff),
        ).fetchall()
        conn.close()

        if len(rows) < TOPIC_MIN_COUNT:
            return False, ""

        # Simple keyword frequency analysis
        word_counts: Dict[str, int] = {}
        stop_words = {
            "de", "la", "el", "en", "que", "es", "un", "una", "los", "las",
            "por", "con", "para", "del", "al", "se", "lo", "como", "más",
            "no", "sí", "si", "me", "mi", "te", "tu", "su", "a", "y", "o",
            "the", "is", "a", "an", "of", "in", "to", "and", "or", "for",
            "qué", "cómo", "cuánto", "dime", "hola", "hey", "ok", "bueno",
        }

        for (text,) in rows:
            if not text:
                continue
            words = text.lower().split()
            for w in words:
                w = w.strip("¿?¡!.,;:")
                if len(w) > 3 and w not in stop_words:
                    word_counts[w] = word_counts.get(w, 0) + 1

        # Find most repeated significant word
        if not word_counts:
            return False, ""

        top_word, top_count = max(word_counts.items(), key=lambda x: x[1])
        if top_count < TOPIC_MIN_COUNT:
            return False, ""

        # Check it wasn't already triggered for this word
        if _already_sent_trigger(user_id, f"convergence:{top_word}"):
            return False, ""

        context = f"Ha mencionado '{top_word}' {top_count} veces en los últimos {TOPIC_WINDOW_DAYS} días"
        return True, context

    except Exception:
        return False, ""


# ---------------------------------------------------------------------------
# Limits enforcement helpers
# ---------------------------------------------------------------------------

def _get_interaction_count(user_id: str) -> int:
    try:
        conn = sqlite3.connect(_USER_DB, timeout=2)
        row = conn.execute(
            "SELECT COUNT(*) FROM interactions WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return 0


def _get_session(user_id: str) -> Optional[Dict]:
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT session_start, last_message, message_count, intervention, last_content "
            "FROM presence_sessions WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        return {
            "session_start": row[0],
            "last_message": row[1],
            "message_count": row[2],
            "intervention": row[3],
            "last_content": row[4],
        }
    except Exception:
        return None


def _cooldown_active(user_id: str) -> bool:
    cutoff = time.time() - (COOLDOWN_HOURS * 3600)
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT 1 FROM presence_sent WHERE user_id = ? AND sent_at > ? LIMIT 1",
            (user_id, cutoff),
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def _already_sent_trigger(user_id: str, trigger_key: str) -> bool:
    """Check if this specific trigger was already sent (prevents repeats)."""
    msg_hash = hashlib.md5(f"{user_id}:{trigger_key}".encode()).hexdigest()
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT 1 FROM presence_sent WHERE msg_hash = ?",
            (msg_hash,),
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Generate intervention + record
# ---------------------------------------------------------------------------

def _generate_and_record(
    user_id: str,
    trigger: str,
    context: str,
) -> Optional[str]:
    """Generate intervention message and record it."""
    # Get user name
    name = ""
    lang = "es"
    try:
        conn = sqlite3.connect(_USER_DB, timeout=2)
        row = conn.execute(
            "SELECT name, language FROM profiles WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        conn.close()
        if row:
            name = (row[0] or "").split()[0] if row[0] else ""
            lang = row[1] or "es"
    except Exception:
        pass

    # Generate message via LLM
    message = _generate_message(trigger, context, name, lang)
    if not message:
        return None

    # Check duplicate (never repeat same message)
    msg_hash = hashlib.md5(message.encode()).hexdigest()
    try:
        conn = _conn()
        exists = conn.execute(
            "SELECT 1 FROM presence_sent WHERE msg_hash = ?", (msg_hash,),
        ).fetchone()
        if exists:
            conn.close()
            return None

        # Record sent
        conn.execute(
            "INSERT INTO presence_sent (user_id, trigger, message, sent_at, msg_hash) VALUES (?,?,?,?,?)",
            (user_id, trigger, message[:500], time.time(), msg_hash),
        )
        # Mark session as intervened
        conn.execute(
            "UPDATE presence_sessions SET intervention = 1 WHERE user_id = ?",
            (user_id,),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("record intervention failed: %s", exc)
        return None

    logger.info(
        "PRESENCE | %s | trigger=%s | user=%s | msg=%s",
        "INTERVENE", trigger, user_id[:20], message[:60],
    )
    return message


def _generate_message(
    trigger: str,
    context: str,
    name: str,
    lang: str,
) -> Optional[str]:
    """Generate a contextual presence message via LLM. Falls back to template."""
    prompt = (
        "[PRESENCIA CONTEXTUAL]\n"
        f"Trigger: {trigger}\n"
        f"Contexto: {context}\n"
        f"Usuario: {name or 'sin nombre'}\n\n"
        "Genera UNA frase corta (máx 2 líneas) que demuestre presencia y contexto.\n"
        "NO preguntes '¿en qué puedo ayudarte?'. NO hagas onboarding.\n"
        "NO uses emojis. NO seas genérico.\n"
        "Solo demuestra que recuerdas y observas."
    )

    # Try LLM
    try:
        from vectrax.intelligence_bridge import is_ready, route_single
        if is_ready():
            result = route_single(prompt)
            if result.get("success") and result.get("content"):
                msg = result["content"].strip()
                if len(msg) > 10 and len(msg) < 300:
                    return msg
    except Exception:
        pass

    # Try OpenAI direct
    try:
        from core.external_call_guard import guarded_call

        def _openai_call():
            import httpx
            api_key = os.environ.get("OPENAI_API_KEY", "")
            if not api_key:
                return None
            r = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": "Eres Vectrax. Hablas poco. Cuando hablas, es porque observaste algo real."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 100,
                    "temperature": 0.6,
                },
                timeout=15,
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
            return None

        msg = guarded_call("openai", _openai_call, timeout=20, fallback=None)
        if msg and len(msg) > 10 and len(msg) < 300:
            return msg
    except Exception:
        pass

    # Fallback: simple template (last resort)
    if trigger == "return_silence" and name:
        return f"{name}, la última vez estábamos en algo. ¿Retomamos?"
    if trigger == "topic_convergence":
        # Extract topic from context
        topic = context.split("'")[1] if "'" in context else "eso"
        return f"Noto que vuelves a {topic}. ¿Quieres que profundice?"

    return None


# ---------------------------------------------------------------------------
# Stats for dashboard
# ---------------------------------------------------------------------------

def get_presence_stats() -> Dict:
    """Return presence layer statistics."""
    try:
        conn = _conn()
        sessions = conn.execute("SELECT COUNT(*) FROM presence_sessions").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM presence_sessions WHERE last_message > ?",
            (time.time() - SESSION_GAP,),
        ).fetchone()[0]
        total_sent = conn.execute("SELECT COUNT(*) FROM presence_sent").fetchone()[0]
        recent_sent = conn.execute(
            "SELECT COUNT(*) FROM presence_sent WHERE sent_at > ?",
            (time.time() - 86400,),
        ).fetchone()[0]
        conn.close()
        return {
            "tracked_users": sessions,
            "active_sessions": active,
            "total_interventions": total_sent,
            "interventions_24h": recent_sent,
        }
    except Exception:
        return {}
