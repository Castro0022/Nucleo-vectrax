"""
Vectrax User Memory
======================
Memoria por usuario: almacena interacciones y extrae datos personales
(nombre, preferencias, idioma) para construir contexto acumulado.

API pública:
  store_memory(user_id, user_input, bot_output)
  get_memory_context(user_id) → str
  get_user_profile(user_id)   → dict
  clear_memory(user_id)

Almacenamiento inicial: dict en memoria (thread-safe).
Diseñado para escalar a persistencia externa sin cambiar la API.

Capa: External — Memoria de usuario
Creado: 2026-03-19
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.user_memory")

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

MAX_HISTORY_PER_USER = 50      # interacciones almacenadas por usuario
MAX_CONTEXT_ENTRIES = 8        # interacciones incluidas en el contexto
MAX_CONTEXT_CHARS = 1500       # caracteres máx. del contexto generado (Capa 2: era 800)
MAX_TURN_CHARS = 280           # caracteres máx. POR TURNO (Capa 2: era 80, frases cortadas)
PROFILE_FIELDS = ("name", "language", "preferences", "interests")


# ---------------------------------------------------------------------------
# Modelo de datos
# ---------------------------------------------------------------------------

@dataclass
class Interaction:
    """Una interacción usuario ↔ Vectrax."""
    user_input: str
    bot_output: str
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.time()


@dataclass
class UserProfile:
    """Perfil extraído de las interacciones del usuario."""
    name: str = ""
    language: str = ""
    preferences: Dict[str, str] = field(default_factory=dict)
    interests: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "language": self.language,
            "preferences": dict(self.preferences),
            "interests": list(self.interests),
        }


# ---------------------------------------------------------------------------
# Extractores de datos personales
# ---------------------------------------------------------------------------

_NAME_PATTERNS = [
    # "me llamo X", "mi nombre es X", "my name is X"
    re.compile(
        r"(?:me\s+llamo|mi\s+nombre\s+es|my\s+name\s+is)\s+"
        r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?)",
        re.IGNORECASE | re.UNICODE,
    ),
    # "soy X" (solo si la siguiente palabra empieza con mayúscula)
    re.compile(
        r"(?:^|\.\s+)[Ss]oy\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?)"
        r"(?:\s*[.,!]|\s*$)",
        re.UNICODE,
    ),
    # "I'm X", "I am X"
    re.compile(
        r"(?:I'?m|I\s+am)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)"
        r"(?:\s*[.,!]|\s*$)",
    ),
]

_PREFERENCE_PATTERNS = [
    # "prefiero X", "me gusta X", "uso X"
    re.compile(
        r"(?:prefiero|me\s+gusta(?:n)?|uso|utilizo|trabajo\s+con)\s+"
        r"(.{3,40}?)(?:\s*[.,;!]|\s*$)",
        re.IGNORECASE,
    ),
    # "I prefer X", "I use X", "I like X"
    re.compile(
        r"(?:I\s+(?:prefer|use|like|work\s+with))\s+"
        r"(.{3,40}?)(?:\s*[.,;!]|\s*$)",
        re.IGNORECASE,
    ),
]

_INTEREST_PATTERNS = [
    # "me interesa X", "estudio X", "investigo X"
    re.compile(
        r"(?:me\s+interesa(?:n)?|estudio|investigo|estoy\s+aprendiendo)\s+"
        r"(.{3,60}?)(?:\s*[.,;!]|\s*$)",
        re.IGNORECASE,
    ),
    # "I'm interested in X", "I'm learning X"
    re.compile(
        r"(?:I'?m\s+(?:interested\s+in|learning|studying))\s+"
        r"(.{3,60}?)(?:\s*[.,;!]|\s*$)",
        re.IGNORECASE,
    ),
]

_LANG_DETECT = re.compile(
    r"[áéíóúñü¡¿]"
    r"|\b(?:el|la|los|las|qué|cómo|hola|buenas|gracias|por|para|como|una?)\b",
    re.IGNORECASE,
)


def _extract_name(text: str) -> str:
    """Intenta extraer un nombre del texto."""
    for pat in _NAME_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1).strip()
    return ""


def _extract_preferences(text: str) -> List[str]:
    """Extrae preferencias mencionadas."""
    results = []
    for pat in _PREFERENCE_PATTERNS:
        for m in pat.finditer(text):
            val = m.group(1).strip()
            if val and len(val) >= 3:
                results.append(val)
    return results


def _extract_interests(text: str) -> List[str]:
    """Extrae intereses mencionados."""
    results = []
    for pat in _INTEREST_PATTERNS:
        for m in pat.finditer(text):
            val = m.group(1).strip()
            if val and len(val) >= 3:
                results.append(val)
    return results


def _detect_lang(text: str) -> str:
    """Detección de idioma — delega al language_gate multi-idioma."""
    try:
        from core.language_gate import detect_language
        detected = detect_language(text)
        if detected != "unknown":
            return detected
    except Exception:
        pass
    # Fallback mínimo
    es_matches = len(_LANG_DETECT.findall(text))
    return "es" if es_matches >= 1 else "en"


# ---------------------------------------------------------------------------
# Almacén de memoria (SQLite persistente + cache en RAM)
# ---------------------------------------------------------------------------

_MEMORY_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "user_memory.db",
)

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS interactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    user_input  TEXT NOT NULL DEFAULT '',
    bot_output  TEXT NOT NULL DEFAULT '',
    timestamp   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_interactions_user ON interactions(user_id);

CREATE TABLE IF NOT EXISTS profiles (
    user_id      TEXT PRIMARY KEY,
    name         TEXT NOT NULL DEFAULT '',
    language     TEXT NOT NULL DEFAULT '',
    preferences  TEXT NOT NULL DEFAULT '{}',
    interests    TEXT NOT NULL DEFAULT '[]',
    updated_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS user_locations (
    user_id     TEXT PRIMARY KEY,
    latitude    REAL NOT NULL,
    longitude   REAL NOT NULL,
    updated_at  REAL NOT NULL
);
"""


class _MemoryStore:
    """Almacén singleton de memoria por usuario — SQLite persistente."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache_profiles: Dict[str, UserProfile] = {}
        self._init_db()
        self._load_profiles_cache()
        logger.info(
            "MemoryStore initialized (SQLite: %s)", _MEMORY_DB_PATH,
        )

    def _init_db(self) -> None:
        """Crear tablas si no existen."""
        import sqlite3
        os.makedirs(os.path.dirname(_MEMORY_DB_PATH), exist_ok=True)
        conn = sqlite3.connect(_MEMORY_DB_PATH)
        conn.executescript(_CREATE_TABLES)
        conn.commit()
        conn.close()

    def _conn(self):
        import sqlite3
        return sqlite3.connect(_MEMORY_DB_PATH)

    def _load_profiles_cache(self) -> None:
        """Cargar perfiles desde SQLite al cache en RAM."""
        import json
        try:
            conn = self._conn()
            rows = conn.execute("SELECT * FROM profiles").fetchall()
            conn.close()
            for row in rows:
                user_id, name, language, prefs_json, interests_json, _ = row
                self._cache_profiles[user_id] = UserProfile(
                    name=name,
                    language=language,
                    preferences=json.loads(prefs_json) if prefs_json else {},
                    interests=json.loads(interests_json) if interests_json else [],
                )
        except Exception as exc:
            logger.warning("Failed to load profiles cache: %s", exc)

    # -- Escritura -----------------------------------------------------------

    def store(
        self,
        user_id: str,
        user_input: str,
        bot_output: str,
    ) -> None:
        """Almacena una interacción y actualiza el perfil. Persiste en SQLite."""
        if not user_id:
            return

        ts = time.time()
        inp = user_input.strip() if user_input else ""
        out = bot_output.strip() if bot_output else ""

        with self._lock:
            # Persistir interacción en SQLite
            try:
                conn = self._conn()
                conn.execute(
                    "INSERT INTO interactions (user_id, user_input, bot_output, timestamp) "
                    "VALUES (?, ?, ?, ?)",
                    (user_id, inp, out, ts),
                )
                # Recortar si excede máximo
                count = conn.execute(
                    "SELECT COUNT(*) FROM interactions WHERE user_id = ?",
                    (user_id,),
                ).fetchone()[0]
                if count > MAX_HISTORY_PER_USER:
                    conn.execute(
                        "DELETE FROM interactions WHERE id IN "
                        "(SELECT id FROM interactions WHERE user_id = ? "
                        "ORDER BY timestamp ASC LIMIT ?)",
                        (user_id, count - MAX_HISTORY_PER_USER),
                    )
                conn.commit()
                conn.close()
            except Exception as exc:
                logger.error("SQLite store failed: %s", exc)

            # Actualizar perfil
            self._update_profile(user_id, inp)

        logger.info("Memory stored | user=%s", user_id[:20])

    def _update_profile(self, user_id: str, text: str) -> None:
        """Actualiza el perfil del usuario con datos extraídos del input."""
        if not text:
            return

        if user_id not in self._cache_profiles:
            self._cache_profiles[user_id] = UserProfile()
        profile = self._cache_profiles[user_id]

        # Nombre
        name = _extract_name(text)
        if name and not profile.name:
            profile.name = name
            logger.info("Profile: name detected | user=%s | name=%s", user_id[:20], name)

        # Idioma: respetar idioma guardado/fijado. NO sobreescribir por detección.
        if not profile.language:
            try:
                from vectrax.identity_anchor import get_locked_language
                locked = get_locked_language(user_id)
                if locked:
                    profile.language = locked
                else:
                    profile.language = _detect_lang(text)
            except ImportError:
                profile.language = _detect_lang(text)

        # Preferencias
        prefs = _extract_preferences(text)
        for p in prefs:
            key = p.lower()[:30]
            if key not in profile.preferences:
                profile.preferences[key] = p
                logger.info("Profile: preference detected | user=%s | pref=%s", user_id[:20], p)

        # Intereses
        interests = _extract_interests(text)
        for i in interests:
            if i.lower() not in {x.lower() for x in profile.interests}:
                profile.interests.append(i)
                if len(profile.interests) > 20:
                    profile.interests = profile.interests[-20:]
                logger.info("Profile: interest detected | user=%s | interest=%s", user_id[:20], i)

        # Persistir perfil en SQLite
        self._save_profile(user_id, profile)

    def _save_profile(self, user_id: str, profile: UserProfile) -> None:
        """Persiste el perfil en SQLite."""
        import json
        try:
            conn = self._conn()
            conn.execute(
                "INSERT OR REPLACE INTO profiles "
                "(user_id, name, language, preferences, interests, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    profile.name,
                    profile.language,
                    json.dumps(profile.preferences, ensure_ascii=False),
                    json.dumps(profile.interests, ensure_ascii=False),
                    time.time(),
                ),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.error("SQLite profile save failed: %s", exc)

    # -- Lectura -------------------------------------------------------------

    def get_context(self, user_id: str) -> str:
        """
        Construye un string de contexto de memoria para el usuario.

        Incluye: perfil + últimas N interacciones resumidas.
        Lee desde SQLite — sobrevive reinicios.
        """
        if not user_id:
            return ""

        with self._lock:
            profile = self._cache_profiles.get(user_id)
            # Leer historia desde SQLite
            history = []
            try:
                conn = self._conn()
                rows = conn.execute(
                    "SELECT user_input, bot_output, timestamp "
                    "FROM interactions WHERE user_id = ? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (user_id, MAX_CONTEXT_ENTRIES),
                ).fetchall()
                conn.close()
                history = [
                    Interaction(user_input=r[0], bot_output=r[1], timestamp=r[2])
                    for r in reversed(rows)
                ]
            except Exception as exc:
                logger.warning("SQLite history read failed: %s", exc)

        parts: List[str] = []

        # Perfil
        if profile:
            profile_parts = []
            if profile.name:
                profile_parts.append(f"Nombre: {profile.name}")
            if profile.language:
                profile_parts.append(f"Idioma: {profile.language}")
            if profile.preferences:
                vals = ", ".join(list(profile.preferences.values())[:5])
                profile_parts.append(f"Preferencias: {vals}")
            if profile.interests:
                vals = ", ".join(profile.interests[:5])
                profile_parts.append(f"Intereses: {vals}")
            if profile_parts:
                parts.append("[Perfil del usuario]\n" + "\n".join(profile_parts))

        # Historia reciente
        # Capa 2 (memoria activa relevante): cada turno se trunca a
        # MAX_TURN_CHARS (antes 80 — cortaba frases a la mitad). El cap
        # total de contexto se aplica en MAX_CONTEXT_CHARS más abajo.
        if history:
            recent = history[-MAX_CONTEXT_ENTRIES:]
            lines = []
            for ix in recent:
                inp = ix.user_input[:MAX_TURN_CHARS] if ix.user_input else ""
                out = ix.bot_output[:MAX_TURN_CHARS] if ix.bot_output else ""
                lines.append(f"- Usuario: {inp}")
                if out:
                    lines.append(f"  Vectrax: {out}")
            parts.append("[Historial reciente]\n" + "\n".join(lines))

        context = "\n\n".join(parts)

        # Truncar si es muy largo
        if len(context) > MAX_CONTEXT_CHARS:
            context = context[:MAX_CONTEXT_CHARS].rsplit("\n", 1)[0]

        return context

    def get_profile(self, user_id: str) -> Dict[str, Any]:
        """Retorna el perfil del usuario como dict."""
        with self._lock:
            profile = self._cache_profiles.get(user_id)
        if not profile:
            return {}
        return profile.to_dict()

    def get_history_count(self, user_id: str) -> int:
        """Número de interacciones almacenadas para un usuario."""
        try:
            conn = self._conn()
            count = conn.execute(
                "SELECT COUNT(*) FROM interactions WHERE user_id = ?",
                (user_id,),
            ).fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0

    def clear(self, user_id: str) -> None:
        """Limpia toda la memoria de un usuario."""
        with self._lock:
            self._cache_profiles.pop(user_id, None)
            try:
                conn = self._conn()
                conn.execute("DELETE FROM interactions WHERE user_id = ?", (user_id,))
                conn.execute("DELETE FROM profiles WHERE user_id = ?", (user_id,))
                conn.commit()
                conn.close()
            except Exception as exc:
                logger.error("SQLite clear failed: %s", exc)
        logger.info("Memory cleared | user=%s", user_id[:20])

    def clear_all(self) -> None:
        """Limpia toda la memoria (testing)."""
        with self._lock:
            self._cache_profiles.clear()
            try:
                conn = self._conn()
                conn.execute("DELETE FROM interactions")
                conn.execute("DELETE FROM profiles")
                conn.commit()
                conn.close()
            except Exception as exc:
                logger.error("SQLite clear_all failed: %s", exc)

    def stats(self) -> Dict[str, Any]:
        """Estadísticas globales del almacén."""
        try:
            conn = self._conn()
            users = conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM interactions",
            ).fetchone()[0]
            total = conn.execute(
                "SELECT COUNT(*) FROM interactions",
            ).fetchone()[0]
            conn.close()
            return {"total_users": users, "total_interactions": total}
        except Exception:
            return {"total_users": 0, "total_interactions": 0}


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_store: Optional[_MemoryStore] = None
_store_lock = threading.Lock()


def _get_store() -> _MemoryStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = _MemoryStore()
    return _store


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def store_memory(user_id: str, user_input: str, bot_output: str) -> None:
    """Almacena una interacción usuario ↔ Vectrax + extrae hechos + absorbe en core."""
    _get_store().store(user_id, user_input, bot_output)
    # Extraer y almacenar hechos de negocio (nombres, cifras, fechas)
    _facts_stored = 0
    try:
        from vectrax.fact_memory import store_facts
        _facts_stored = store_facts(user_id, user_input)
        if _facts_stored:
            logger.info("Facts extracted | user=%s | count=%d", user_id[:20], _facts_stored)
    except Exception as exc:
        logger.debug("Fact extraction failed: %s", exc)
    # Cognitive bridge: auto-create scheduler tasks from temporal facts
    if _facts_stored:
        try:
            from core.cognitive_bridge import bridge_facts_to_scheduler
            # Extract chat_id from user_id (format: "tg:XXXXX")
            _chat_id = 0
            if user_id.startswith("tg:"):
                try:
                    _chat_id = int(user_id.split(":", 1)[1])
                except ValueError:
                    pass
            if _chat_id:
                bridged = bridge_facts_to_scheduler(user_id, _chat_id, user_input, _facts_stored)
                if bridged:
                    logger.info("Bridge: %d auto-tasks created | user=%s", bridged, user_id[:20])
        except Exception as exc:
            logger.debug("Cognitive bridge failed: %s", exc)
    # Detectar preferencias reveladas de contactos (detector híbrido)
    try:
        from core.preference_tracker import detect_and_store
        pref_stored = detect_and_store(user_id, user_input)
        if pref_stored:
            logger.info("Preferences extracted | user=%s | count=%d", user_id[:20], pref_stored)
    except Exception as exc:
        logger.debug("Preference detection failed: %s", exc)
    # Absorber en core_memory (memoria permanente indestructible)
    try:
        from vectrax.core_memory import absorb
        absorbed = absorb(user_id, user_input, bot_output)
        if absorbed:
            logger.info("Core absorbed | user=%s | count=%d", user_id[:20], absorbed)
    except Exception as exc:
        logger.debug("Core absorb failed: %s", exc)


def get_memory_context(user_id: str) -> str:
    """Retorna contexto de memoria acumulado para el usuario."""
    return _get_store().get_context(user_id)


def get_user_profile(user_id: str) -> Dict[str, Any]:
    """Retorna el perfil extraído del usuario."""
    return _get_store().get_profile(user_id)


def get_history_count(user_id: str) -> int:
    """Número de interacciones almacenadas."""
    return _get_store().get_history_count(user_id)


def clear_memory(user_id: str) -> None:
    """Limpia la memoria de un usuario."""
    _get_store().clear(user_id)


def clear_all_memory() -> None:
    """Limpia toda la memoria (testing)."""
    _get_store().clear_all()


def memory_stats() -> Dict[str, Any]:
    """Estadísticas globales."""
    return _get_store().stats()


# ---------------------------------------------------------------------------
# Ubicación del usuario (persistente en SQLite)
# ---------------------------------------------------------------------------

def store_user_location(user_id: str, lat: float, lng: float) -> None:
    """Guarda la ubicación del usuario. Sobrevive reinicios."""
    if not user_id:
        return
    try:
        import sqlite3
        conn = sqlite3.connect(_MEMORY_DB_PATH)
        conn.execute(
            "INSERT OR REPLACE INTO user_locations (user_id, latitude, longitude, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, lat, lng, time.time()),
        )
        conn.commit()
        conn.close()
        logger.info("Location stored | user=%s | lat=%.4f lng=%.4f", user_id[:20], lat, lng)
    except Exception as exc:
        logger.error("Failed to store location: %s", exc)


def get_user_location(user_id: str) -> Optional[Dict[str, float]]:
    """Obtiene la última ubicación conocida del usuario. Retorna {lat, lng} o None."""
    if not user_id:
        return None
    try:
        import sqlite3
        conn = sqlite3.connect(_MEMORY_DB_PATH)
        row = conn.execute(
            "SELECT latitude, longitude FROM user_locations WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        conn.close()
        if row:
            return {"lat": row[0], "lng": row[1]}
    except Exception as exc:
        logger.debug("Failed to get location: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Resolver pre-LLM — respuesta directa desde memoria
# ---------------------------------------------------------------------------

_MEMORY_QUERIES: list = [
    # (patrón, campo de perfil, plantilla ES, plantilla EN)
    # — Nombre
    (
        re.compile(
            r"(?:c[oó]mo\s+me\s+llamo"
            r"|cu[aá]l\s+es\s+mi\s+nombre"
            r"|what(?:'s|\s+is)\s+my\s+name"
            r"|qui[eé]n\s+soy"
            r"|who\s+am\s+I"
            r"|sabes\s+(?:c[oó]mo\s+me\s+llamo|mi\s+nombre)"
            r"|me\s+conoces"
            r"|sabes\s+qui[eé]n\s+soy)",
            re.IGNORECASE,
        ),
        "name",
        "Te llamas {value}.",
        "Your name is {value}.",
    ),
    # — Idioma
    (
        re.compile(
            r"(?:qu[eé]\s+idioma\s+(?:hablo|uso)"
            r"|en\s+qu[eé]\s+idioma"
            r"|what\s+language)",
            re.IGNORECASE,
        ),
        "language",
        "Tu idioma detectado es {value}.",
        "Your detected language is {value}.",
    ),
    # — Preferencias
    (
        re.compile(
            r"(?:cu[aá]les\s+son\s+mis\s+preferencias"
            r"|qu[eé]\s+(?:prefiero|me\s+gusta)"
            r"|what\s+(?:do\s+I\s+(?:prefer|like)|are\s+my\s+preferences)"
            r"|mis\s+preferencias"
            r"|sabes\s+(?:qu[eé]\s+(?:prefiero|me\s+gusta)|mis\s+preferencias))",
            re.IGNORECASE,
        ),
        "preferences",
        "Tus preferencias registradas: {value}.",
        "Your registered preferences: {value}.",
    ),
    # — Intereses
    (
        re.compile(
            r"(?:cu[aá]les\s+son\s+mis\s+intereses"
            r"|qu[eé]\s+me\s+interesa"
            r"|what\s+(?:am\s+I\s+interested\s+in|are\s+my\s+interests)"
            r"|mis\s+intereses"
            r"|sabes\s+(?:qu[eé]\s+me\s+interesa|mis\s+intereses))",
            re.IGNORECASE,
        ),
        "interests",
        "Tus intereses registrados: {value}.",
        "Your registered interests: {value}.",
    ),
    # — Qué sabes de mí (resumen completo)
    (
        re.compile(
            r"(?:qu[eé]\s+sabes\s+(?:de\s+m[ií]|sobre\s+m[ií])"
            r"|what\s+do\s+you\s+know\s+about\s+me"
            r"|qu[eé]\s+(?:recuerdas|tienes)\s+(?:de|sobre)\s+m[ií])",
            re.IGNORECASE,
        ),
        "_summary",
        None,
        None,
    ),
]


# Frases normalizadas para matching directo (sin acentos, minúsculas)
_NAME_QUERIES = [
    "quien soy", "quién soy", "como me llamo", "cómo me llamo",
    "cual es mi nombre", "cuál es mi nombre", "what is my name",
    "what's my name", "who am i", "sabes quien soy", "sabes quién soy",
    "sabes como me llamo", "sabes cómo me llamo", "me conoces",
    "sabes mi nombre", "recuerdas mi nombre", "te acuerdas de mi nombre",
    "recuerdas quien soy", "recuerdas quién soy",
]

_PREF_QUERIES = [
    "cuales son mis preferencias", "cuáles son mis preferencias",
    "que prefiero", "qué prefiero", "que me gusta", "qué me gusta",
    "mis preferencias", "what do i prefer", "what are my preferences",
    "what do i like", "sabes que prefiero", "sabes qué prefiero",
    "sabes mis preferencias",
]

_INTEREST_QUERIES = [
    "cuales son mis intereses", "cuáles son mis intereses",
    "que me interesa", "qué me interesa", "mis intereses",
    "what am i interested in", "what are my interests",
    "sabes que me interesa", "sabes qué me interesa",
    "sabes mis intereses",
]

_SUMMARY_QUERIES = [
    "que sabes de mi", "qué sabes de mí", "que sabes de mí",
    "qué sabes de mi", "que sabes sobre mi", "qué sabes sobre mí",
    "what do you know about me", "que recuerdas de mi",
    "qué recuerdas de mí", "que tienes de mi", "qué tienes de mí",
    "que tienes sobre mi", "qué tienes sobre mí",
]

_LANG_QUERIES = [
    "que idioma hablo", "qué idioma hablo", "en que idioma",
    "en qué idioma", "what language",
]


def _normalize(text: str) -> str:
    """Normaliza input: minúscula, sin puntuación de bordes, espacios limpios."""
    t = text.lower().strip()
    # Quitar signos de interrogación/exclamación de bordes
    t = t.strip("¿?¡!.,;: ")
    # Colapsar espacios múltiples
    t = re.sub(r"\s+", " ", t)
    return t


def _match_any(normalized: str, phrases: list) -> bool:
    """True si el texto normalizado contiene alguna de las frases."""
    return any(q in normalized for q in phrases)


def _memory_response(text: str) -> Dict[str, str]:
    """Empaqueta una respuesta resuelta por memoria con bandera de origen."""
    return {"text": text, "source": "memory"}


def _fact_response(result: Dict[str, str]) -> Dict[str, str]:
    """Empaqueta una respuesta resuelta por hechos."""
    return {"text": result["text"], "source": "facts"}


def _get_response_lang(user_id: str, user_input: str) -> str:
    """Obtiene el idioma de respuesta: locked > profile > detectado > 'es'."""
    # 1. Language lock de sesión (prioridad absoluta)
    try:
        from vectrax.identity_anchor import get_locked_language
        locked = get_locked_language(user_id)
        if locked:
            return locked
    except Exception:
        pass
    # 2. Language gate completo (incluye perfil persistente)
    try:
        from core.language_gate import get_user_language
        return get_user_language(user_id, user_input)
    except Exception:
        pass
    # 3. Fallback local
    detected = _detect_lang(user_input)
    return detected if detected != "en" or len(user_input.split()) > 3 else "es"


# Consultas que piden el alma / resumen profundo de core_memory
_CORE_QUERIES = [
    "que sabes de mi", "qué sabes de mí",
    "que sabes sobre mi", "qué sabes sobre mí",
    "que recuerdas de mi", "qué recuerdas de mí",
    "que tienes de mi", "qué tienes de mí",
    "que tienes sobre mi", "qué tienes sobre mí",
    "what do you know about me",
    "quien soy para ti", "quién soy para ti",
    "que soy para ti", "qué soy para ti",
    "me conoces", "sabes quien soy", "sabes quién soy",
]


def resolve_with_memory(user_id: str, user_input: str):
    """
    Resolver pre-LLM: responde directamente desde la memoria del usuario.

    Prioridad:
      1. Hechos de negocio (fact_memory)
      2. Core memory (alma — resumen profundo)
      3. Perfil básico (nombre, preferencias, intereses)

    Reglas estrictas:
      - Si memoria TIENE el dato → retorna respuesta directa.
      - Si memoria NO TIENE el dato → retorna "" para caer al LLM.
      - NUNCA genera mensajes internos ("no tengo", "aún no", etc.).
      - Usa el idioma fijado del usuario (locked language).

    Retorna:
      - {"text": "...", "source": "memory"} si la memoria resuelve.
      - "" (cadena vacía) si debe caer al siguiente camino.
    """
    if not user_id or not user_input:
        return ""

    # ── HECHOS DE NEGOCIO (segundo cerebro) ─ prioridad máxima ───
    try:
        from vectrax.fact_memory import query_facts
        fact_result = query_facts(user_id, user_input)
        if fact_result:
            logger.info(
                "Fact resolve | user=%s | source=facts | input=%r",
                user_id[:20], user_input[:60],
            )
            return _fact_response(fact_result)
    except Exception as exc:
        logger.debug("Fact query failed: %s", exc)

    profile = get_user_profile(user_id)
    if not profile:
        return ""

    normalized = _normalize(user_input)
    lang = _get_response_lang(user_id, user_input)

    # ── Alma / resumen profundo (PRIMERO — antes de nombre) ───────
    if _match_any(normalized, _CORE_QUERIES):
        try:
            from vectrax.core_memory import get_living_response
            living = get_living_response(user_id, lang)
            if living:
                logger.info(
                    "Core LIVING resolve | user=%s | input=%r",
                    user_id[:20], user_input[:60],
                )
                return _memory_response(living)
        except Exception:
            pass

    # ── Nombre ────────────────────────────────────────────────────────
    if _match_any(normalized, _NAME_QUERIES):
        name = profile.get("name", "")
        if name:
            logger.info(
                "Memory resolve | user=%s | field=name | input=%r",
                user_id[:20], user_input[:60],
            )
            if lang == "es":
                return _memory_response(f"Eres {name}.")
            return _memory_response(f"You are {name}.")
        # Sin dato → caer al LLM (no generar respuesta interna)
        return ""

    # ── Preferencias ──────────────────────────────────────────
    if _match_any(normalized, _PREF_QUERIES):
        prefs = profile.get("preferences", {})
        if isinstance(prefs, dict) and prefs:
            vals = ", ".join(list(prefs.values())[:5])
            logger.info(
                "Memory resolve | user=%s | field=preferences | input=%r",
                user_id[:20], user_input[:60],
            )
            if lang == "es":
                return _memory_response(f"Tus preferencias registradas: {vals}.")
            return _memory_response(f"Your registered preferences: {vals}.")
        return ""

    # ── Intereses ─────────────────────────────────────────────
    if _match_any(normalized, _INTEREST_QUERIES):
        interests = profile.get("interests", [])
        if isinstance(interests, list) and interests:
            vals = ", ".join(interests[:5])
            logger.info(
                "Memory resolve | user=%s | field=interests | input=%r",
                user_id[:20], user_input[:60],
            )
            if lang == "es":
                return _memory_response(f"Tus intereses registrados: {vals}.")
            return _memory_response(f"Your registered interests: {vals}.")
        return ""

    # ── Idioma ────────────────────────────────────────────────
    if _match_any(normalized, _LANG_QUERIES):
        language = profile.get("language", "")
        if language:
            logger.info(
                "Memory resolve | user=%s | field=language | input=%r",
                user_id[:20], user_input[:60],
            )
            if lang == "es":
                return _memory_response(f"Tu idioma detectado es {language}.")
            return _memory_response(f"Your detected language is {language}.")
        return ""

    # ── Resumen completo ──────────────────────────────────────────
    if _match_any(normalized, _SUMMARY_QUERIES):
        # Intentar voz viva desde core_memory
        try:
            from vectrax.core_memory import get_living_response
            living = get_living_response(user_id, lang)
            if living:
                logger.info(
                    "Core LIVING resolve | user=%s | input=%r",
                    user_id[:20], user_input[:60],
                )
                return _memory_response(living)
        except Exception:
            pass
        # Fallback al resumen básico de profile
        summary = _build_summary(profile, lang)
        if profile.get("name") or profile.get("preferences") or profile.get("interests"):
            return _memory_response(summary)
        return ""

    # ── Fallback regex para variaciones no cubiertas ──────────
    for pattern, field_name, tpl_es, tpl_en in _MEMORY_QUERIES:
        if not pattern.search(user_input):
            continue

        if field_name == "_summary":
            if profile.get("name") or profile.get("preferences") or profile.get("interests"):
                return _memory_response(_build_summary(profile, lang))
            return ""

        value = profile.get(field_name, "")
        if isinstance(value, dict) and value:
            value = ", ".join(list(value.values())[:5])
        elif isinstance(value, list) and value:
            value = ", ".join(value[:5])

        if not value:
            # Sin dato → caer al LLM (no generar respuesta interna)
            return ""

        tpl = tpl_es if lang == "es" else tpl_en
        answer = tpl.format(value=value)
        logger.info(
            "Memory resolve | user=%s | field=%s | input=%r",
            user_id[:20], field_name, user_input[:60],
        )
        return _memory_response(answer)

    return ""


def _build_summary(profile: Dict[str, Any], lang: str) -> str:
    """Construye un resumen de lo que Vectrax sabe del usuario."""
    parts = []
    if profile.get("name"):
        parts.append(f"Nombre: {profile['name']}")
    if profile.get("language"):
        parts.append(f"Idioma: {profile['language']}")
    if profile.get("preferences"):
        prefs = profile["preferences"]
        vals = ", ".join(list(prefs.values())[:5]) if isinstance(prefs, dict) else str(prefs)
        parts.append(f"Preferencias: {vals}")
    if profile.get("interests"):
        ints = profile["interests"]
        vals = ", ".join(ints[:5]) if isinstance(ints, list) else str(ints)
        parts.append(f"Intereses: {vals}")

    if not parts:
        # Sin datos reales → retornar vacío (no generar mensaje interno)
        return ""

    if lang == "es":
        header = "Esto es lo que tengo registrado sobre ti:"
    else:
        header = "Here's what I have on record about you:"
    return header + "\n" + "\n".join(f"- {p}" for p in parts)
