"""
Word Gravity Index (WGI)
=========================
Cada palabra importante acumula masa gravitacional basada en frecuencia,
convergencias y conexiones semánticas. Cuando aparece en un input, activa
toda su constelación asociada.

El índice es dual:
  - scope="global"   → palabras del sistema (VECTRAX, núcleo, etc.)
  - scope=<user_id>  → palabras personalizadas por usuario (polisemia)

Creado: 2026-06-08
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.word_gravity")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GRAVITY_ACTIVATION_THRESHOLD = 0.6   # masa mínima para activar constelación
GRAVITY_LOCK_THRESHOLD = 0.85        # masa mínima para prohibir ONLINE
DECAY_FACTOR = 0.995                 # decay diario para palabras inactivas
DECAY_GRACE_DAYS = 7                 # días sin activación antes de decaer
MIN_MASS = 0.05                      # masa mínima (debajo se elimina)
MAX_CONSTELLATION_IDS = 50           # máx star/pattern IDs por palabra

SCOPE_GLOBAL = "global"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "user_memory.db",
)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS word_gravity (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    word             TEXT    NOT NULL,
    scope            TEXT    NOT NULL DEFAULT 'global',
    mass             REAL    NOT NULL DEFAULT 0.0,
    activation_count INTEGER NOT NULL DEFAULT 0,
    last_activated   REAL    NOT NULL DEFAULT 0.0,
    constellation_ids TEXT   NOT NULL DEFAULT '[]',
    category         TEXT    NOT NULL DEFAULT 'concept',
    created_at       REAL    NOT NULL,
    updated_at       REAL    NOT NULL,
    UNIQUE(word, scope)
);
CREATE INDEX IF NOT EXISTS idx_wg_word ON word_gravity(word);
CREATE INDEX IF NOT EXISTS idx_wg_scope ON word_gravity(scope);
CREATE INDEX IF NOT EXISTS idx_wg_mass ON word_gravity(mass DESC);
"""

_initialized = False


def _conn() -> sqlite3.Connection:
    global _initialized
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=3)
    conn.execute("PRAGMA journal_mode=WAL")
    if not _initialized:
        conn.executescript(_CREATE_TABLE)
        _initialized = True
    return conn


# ---------------------------------------------------------------------------
# Normalización
# ---------------------------------------------------------------------------

def normalize_word(word: str) -> str:
    """Lowercase, strip accents, strip non-alpha."""
    w = word.lower().strip()
    # Remove accents
    nfkd = unicodedata.normalize("NFKD", w)
    w = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Keep only alphanumeric
    w = re.sub(r"[^a-z0-9]", "", w)
    return w


# ---------------------------------------------------------------------------
# Stopwords (es + en) — palabras que nunca acumulan masa
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset({
    # Español
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del",
    "en", "con", "por", "para", "al", "a", "y", "o", "que", "se", "es",
    "su", "lo", "como", "mas", "pero", "si", "no", "ya", "ha", "muy",
    "me", "mi", "te", "tu", "nos", "le", "les", "este", "esta", "esto",
    "ese", "esa", "eso", "aquel", "aquella", "aquello", "hay", "ser",
    "estar", "tener", "hacer", "ir", "ver", "dar", "saber", "poder",
    "querer", "decir", "son", "era", "fue", "sido", "tiene", "tiene",
    "han", "hemos", "habia", "donde", "cuando", "porque", "sin",
    "sobre", "entre", "tambien", "asi", "bien", "cada", "todo", "toda",
    "todos", "todas", "otro", "otra", "otros", "otras", "cual", "cuales",
    "aqui", "ahi", "alli", "desde", "hasta", "ante", "bajo", "contra",
    "segun", "hacia", "mediante", "durante", "tras", "quien", "quienes",
    # English
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "can", "could", "must", "to", "of", "in",
    "for", "on", "with", "at", "by", "from", "as", "into", "through",
    "during", "before", "after", "above", "below", "between", "out",
    "off", "up", "down", "and", "but", "or", "nor", "not", "so", "yet",
    "both", "either", "neither", "each", "every", "all", "any", "few",
    "more", "most", "other", "some", "such", "than", "too", "very",
    "just", "about", "also", "then", "there", "here", "this", "that",
    "these", "those", "it", "its", "i", "me", "my", "we", "our", "you",
    "your", "he", "him", "his", "she", "her", "they", "them", "their",
    "what", "which", "who", "whom", "how", "when", "where", "why",
})

# Minimum word length to be considered significant
_MIN_WORD_LEN = 3


def extract_keywords(text: str) -> List[str]:
    """Extract significant words from text, filtering stopwords."""
    tokens = re.findall(r"[a-záéíóúñüA-ZÁÉÍÓÚÑÜ]+", text.lower())
    keywords = []
    seen = set()
    for token in tokens:
        normalized = normalize_word(token)
        if (
            len(normalized) >= _MIN_WORD_LEN
            and normalized not in _STOPWORDS
            and normalized not in seen
        ):
            seen.add(normalized)
            keywords.append(normalized)
    return keywords


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@dataclass
class WordEntry:
    """A single word in the gravity index."""
    word: str
    scope: str = SCOPE_GLOBAL
    mass: float = 0.0
    activation_count: int = 0
    last_activated: float = 0.0
    constellation_ids: List[str] = field(default_factory=list)
    category: str = "concept"


def upsert_word(
    word: str,
    scope: str = SCOPE_GLOBAL,
    delta: float = 0.1,
    category: str = "concept",
    constellation_id: Optional[str] = None,
) -> float:
    """
    Insert or update a word in the gravity index.

    Returns the new mass after update.
    """
    normalized = normalize_word(word)
    if not normalized or normalized in _STOPWORDS:
        return 0.0

    now = time.time()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT mass, constellation_ids FROM word_gravity "
            "WHERE word = ? AND scope = ?",
            (normalized, scope),
        ).fetchone()

        if row is None:
            # Insert new
            new_mass = min(1.0, max(0.0, delta))
            cids = json.dumps([constellation_id] if constellation_id else [])
            conn.execute(
                "INSERT INTO word_gravity "
                "(word, scope, mass, activation_count, last_activated, "
                " constellation_ids, category, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)",
                (normalized, scope, new_mass, now, cids, category, now, now),
            )
            conn.commit()
            return new_mass

        # Update existing
        old_mass = row[0]
        new_mass = min(1.0, old_mass + delta)
        existing_cids: List[str] = json.loads(row[1] or "[]")
        if constellation_id and constellation_id not in existing_cids:
            existing_cids.append(constellation_id)
            if len(existing_cids) > MAX_CONSTELLATION_IDS:
                existing_cids = existing_cids[-MAX_CONSTELLATION_IDS:]
        conn.execute(
            "UPDATE word_gravity SET mass = ?, activation_count = activation_count + 1, "
            "last_activated = ?, constellation_ids = ?, updated_at = ? "
            "WHERE word = ? AND scope = ?",
            (new_mass, now, json.dumps(existing_cids), now, normalized, scope),
        )
        conn.commit()
        return new_mass
    finally:
        conn.close()


def get_word_mass(word: str, scope: str = SCOPE_GLOBAL) -> float:
    """Get the mass of a word in a given scope. Returns 0.0 if not found."""
    normalized = normalize_word(word)
    if not normalized:
        return 0.0
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT mass FROM word_gravity WHERE word = ? AND scope = ?",
            (normalized, scope),
        ).fetchone()
        return row[0] if row else 0.0
    finally:
        conn.close()


def get_word_entry(word: str, scope: str = SCOPE_GLOBAL) -> Optional[WordEntry]:
    """Get full word entry. Returns None if not found."""
    normalized = normalize_word(word)
    if not normalized:
        return None
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT word, scope, mass, activation_count, last_activated, "
            "constellation_ids, category FROM word_gravity "
            "WHERE word = ? AND scope = ?",
            (normalized, scope),
        ).fetchone()
        if not row:
            return None
        return WordEntry(
            word=row[0],
            scope=row[1],
            mass=row[2],
            activation_count=row[3],
            last_activated=row[4],
            constellation_ids=json.loads(row[5] or "[]"),
            category=row[6],
        )
    finally:
        conn.close()


def get_effective_mass(word: str, user_id: str) -> Tuple[float, str]:
    """
    Get effective mass for a word, resolving polysemy.

    Checks user scope first, then global. Returns max(user, global).
    Returns (mass, winning_scope).
    """
    normalized = normalize_word(word)
    if not normalized:
        return 0.0, SCOPE_GLOBAL

    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT scope, mass FROM word_gravity "
            "WHERE word = ? AND scope IN (?, ?)",
            (normalized, user_id, SCOPE_GLOBAL),
        ).fetchall()
        if not rows:
            return 0.0, SCOPE_GLOBAL
        best_mass = 0.0
        best_scope = SCOPE_GLOBAL
        for scope, mass in rows:
            if mass > best_mass:
                best_mass = mass
                best_scope = scope
        return best_mass, best_scope
    finally:
        conn.close()


def get_constellation_ids(word: str, scope: str = SCOPE_GLOBAL) -> List[str]:
    """Get constellation IDs for a word."""
    entry = get_word_entry(word, scope)
    return entry.constellation_ids if entry else []


def record_activation(word: str, scope: str = SCOPE_GLOBAL) -> None:
    """Record that a word was activated (used in a query)."""
    normalized = normalize_word(word)
    if not normalized:
        return
    now = time.time()
    conn = _conn()
    try:
        conn.execute(
            "UPDATE word_gravity SET activation_count = activation_count + 1, "
            "last_activated = ?, updated_at = ? "
            "WHERE word = ? AND scope = ?",
            (now, now, normalized, scope),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Decay
# ---------------------------------------------------------------------------

def apply_decay() -> int:
    """
    Apply natural decay to inactive words.
    Returns number of words affected.
    """
    now = time.time()
    grace_cutoff = now - (DECAY_GRACE_DAYS * 86400)
    conn = _conn()
    try:
        # Decay inactive words (except global identity — never decay)
        cursor = conn.execute(
            "UPDATE word_gravity SET mass = mass * ?, updated_at = ? "
            "WHERE last_activated < ? "
            "AND NOT (scope = ? AND category = 'identity')",
            (DECAY_FACTOR, now, grace_cutoff, SCOPE_GLOBAL),
        )
        decayed = cursor.rowcount

        # Remove words below minimum mass
        cursor2 = conn.execute(
            "DELETE FROM word_gravity WHERE mass < ? "
            "AND NOT (scope = ? AND category = 'identity')",
            (MIN_MASS, SCOPE_GLOBAL),
        )
        pruned = cursor2.rowcount

        conn.commit()
        if decayed > 0 or pruned > 0:
            logger.info(
                "WGI decay: %d decayed, %d pruned", decayed, pruned,
            )
        return decayed + pruned
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Seed — palabras fundacionales del sistema
# ---------------------------------------------------------------------------

_SEED_WORDS: List[Tuple[str, float, str]] = [
    # (word, mass, category)
    ("vectrax", 0.98, "identity"),
    ("mercado", 0.88, "domain"),
    ("memoria", 0.85, "concept"),
    ("convergencia", 0.87, "concept"),
    ("convergencias", 0.87, "concept"),
    ("estrella", 0.83, "concept"),
    ("estrellas", 0.83, "concept"),
    ("constelacion", 0.83, "concept"),
    ("constelaciones", 0.83, "concept"),
    ("nucleo", 0.85, "concept"),
    ("gravitacional", 0.82, "concept"),
    ("identidad", 0.84, "concept"),
    ("observador", 0.80, "concept"),
    ("creador", 0.90, "identity"),
    ("mario", 0.92, "identity"),
    ("universo", 0.80, "concept"),
    ("gravedad", 0.80, "concept"),
    ("patron", 0.75, "concept"),
    ("patrones", 0.75, "concept"),
    ("motor", 0.72, "concept"),
    ("motores", 0.72, "concept"),
    ("soberania", 0.78, "concept"),
    ("presencia", 0.76, "concept"),
    ("dashboard", 0.70, "domain"),
    ("worker", 0.70, "domain"),
    ("pipeline", 0.70, "domain"),
    ("telegram", 0.68, "domain"),
]


def seed_global_index() -> int:
    """
    Initialize the global word gravity index with foundational words.
    Only inserts if the word doesn't already exist in global scope.
    Returns number of words seeded.
    """
    now = time.time()
    conn = _conn()
    seeded = 0
    try:
        for word, mass, category in _SEED_WORDS:
            normalized = normalize_word(word)
            existing = conn.execute(
                "SELECT mass FROM word_gravity WHERE word = ? AND scope = ?",
                (normalized, SCOPE_GLOBAL),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO word_gravity "
                    "(word, scope, mass, activation_count, last_activated, "
                    " constellation_ids, category, created_at, updated_at) "
                    "VALUES (?, ?, ?, 0, 0, '[]', ?, ?, ?)",
                    (normalized, SCOPE_GLOBAL, mass, category, now, now),
                )
                seeded += 1
        conn.commit()
        if seeded > 0:
            logger.info("WGI seeded: %d global words", seeded)
        return seeded
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def get_top_words(
    scope: str = SCOPE_GLOBAL,
    limit: int = 20,
) -> List[Tuple[str, float, str]]:
    """Get top words by mass. Returns [(word, mass, category), ...]."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT word, mass, category FROM word_gravity "
            "WHERE scope = ? ORDER BY mass DESC LIMIT ?",
            (scope, limit),
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]
    finally:
        conn.close()


def get_index_stats() -> Dict[str, int]:
    """Get basic stats about the word gravity index."""
    conn = _conn()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM word_gravity"
        ).fetchone()[0]
        global_count = conn.execute(
            "SELECT COUNT(*) FROM word_gravity WHERE scope = ?",
            (SCOPE_GLOBAL,),
        ).fetchone()[0]
        user_count = conn.execute(
            "SELECT COUNT(DISTINCT scope) FROM word_gravity WHERE scope != ?",
            (SCOPE_GLOBAL,),
        ).fetchone()[0]
        return {
            "total_words": total,
            "global_words": global_count,
            "user_scopes": user_count,
        }
    finally:
        conn.close()
