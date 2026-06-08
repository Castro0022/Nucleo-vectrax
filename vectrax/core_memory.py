"""
Vectrax Core Memory — Yo Existo Aquí Dentro
================================================
Memoria permanente e indestructible. Lo que entra aquí NUNCA se borra.
Es el equivalente a la identidad profunda que Vectrax tiene de cada usuario.

Diferencia con las otras memorias:
  - interactions: rota (máx 50), es historial conversacional temporal
  - user_facts:   hechos de negocio (deudas, reuniones, notas)
  - profiles:     datos básicos (nombre, idioma)
  - core_memory:  TODO lo esencial, consolidado, permanente, con peso

Cada entrada tiene un `weight` (0.0–1.0) que indica qué tan importante
es para la identidad del usuario. El weight crece con la repetición
y la confirmación.

API:
  absorb(user_id, text, bot_response)  — evalúa y absorbe si es importante
  get_soul(user_id)                    — retorna el contexto-alma del usuario
  get_core_entries(user_id)            — todas las entradas del core

El core_memory se inyecta en build_prompt() como la primera capa
de contexto — antes de la memoria conversacional, antes de los hechos.

Creado: 2026-03-31
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger("vectrax.core_memory")

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "user_memory.db",
)

_CREATE_CORE = """
CREATE TABLE IF NOT EXISTS core_memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    category    TEXT NOT NULL,
    content     TEXT NOT NULL,
    weight      REAL NOT NULL DEFAULT 0.5,
    source      TEXT NOT NULL DEFAULT '',
    times_confirmed INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_core_user ON core_memory(user_id);
CREATE INDEX IF NOT EXISTS idx_core_cat ON core_memory(user_id, category);
"""


def _conn():
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=3)
    conn.executescript(_CREATE_CORE)
    return conn


# ---------------------------------------------------------------------------
# Categorías de memoria core
# ---------------------------------------------------------------------------

CATEGORIES = {
    "identity":     1.0,   # nombre, quién es, cómo se define
    "relationship": 0.9,   # personas importantes para el usuario
    "work":         0.85,  # qué hace, su negocio, su profesión
    "preference":   0.7,   # preferencias declaradas
    "goal":         0.8,   # objetivos, planes, aspiraciones
    "emotion":      0.6,   # cómo se siente, estados emocionales recurrentes
    "habit":        0.65,  # patrones de comportamiento
    "fact":         0.5,   # datos concretos (ya cubiertos por fact_memory)
}


# ---------------------------------------------------------------------------
# Extractores de importancia
# ---------------------------------------------------------------------------

@dataclass
class CoreExtraction:
    """Un fragmento de memoria core extraído del texto."""
    category: str
    content: str
    weight: float
    source: str = "conversation"


# Patrones que detectan información digna de core_memory
_EXTRACTORS: list = [
    # Identidad: "me llamo X", "mi nombre es X", "my name is X"
    (
        re.compile(
            r"(?:me\s+llamo|mi\s+nombre\s+es|my\s+name\s+is)\s+"
            r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)*)",
            re.IGNORECASE | re.UNICODE,
        ),
        "identity",
        lambda m: f"Se llama {m.group(1)}.",
        1.0,
    ),
    # "soy X" donde X es nombre propio (mayúscula)
    (
        re.compile(
            r"\b[Ss]oy\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?)"
            r"(?:\s*[.,!]|\s*$)",
            re.UNICODE,
        ),
        "identity",
        lambda m: f"Se llama {m.group(1)}.",
        1.0,
    ),
    # Profesión / trabajo: "soy desarrollador", "soy un ingeniero",
    # "trabajo como/en X", "me dedico a X"
    (
        re.compile(
            r"(?:\b[Ss]oy\s+(?:un\s+)?([a-záéíóúñ]\w+(?:\s+\w+){0,4}))"
            r"|(?:(?:[Tt]rabajo\s+(?:como|de|en)|[Mm]e\s+dedico\s+a)\s+(.{3,80}?)(?:[.,;]|$))"
            r"|(?:[Mm]i\s+(?:profesi[oó]n|trabajo|empresa|negocio)\s+(?:es|se\s+llama)\s+(.{3,80}?)(?:[.,;]|$))"
            r"|(?:[Ii]\s+(?:work\s+(?:as|at|in)|am\s+a)\s+(.{3,80}?)(?:[.,;]|$))",
            re.UNICODE,
        ),
        "work",
        lambda m: f"Trabaja como {(m.group(1) or m.group(2) or m.group(3) or m.group(4) or '').strip().rstrip('.')}.",
        0.85,
    ),
    # Relaciones personales
    (
        re.compile(
            r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)\s+es\s+mi\s+"
            r"((?:esposa?|marido|pareja|hijo|hija|padre|madre|hermano|hermana|"
            r"amigo|amiga|socio|socia|jefe|contadora?|abogada?|cliente|"
            r"wife|husband|partner|son|daughter|father|mother|friend|boss)\w*)",
            re.IGNORECASE | re.UNICODE,
        ),
        "relationship",
        lambda m: f"{m.group(1)} es su {m.group(2)}.",
        0.9,
    ),
    # Metas / objetivos / proyectos
    (
        re.compile(
            r"(?:quiero|necesito|mi\s+(?:meta|objetivo|plan)\s+es|"
            r"estoy\s+(?:buscando|intentando|trabajando\s+en|construyendo|desarrollando|creando)|"
            r"i\s+(?:want|need)\s+to|my\s+goal\s+is|i'?m\s+(?:building|creating|developing|working\s+on))\s+(.{5,120})",
            re.IGNORECASE,
        ),
        "goal",
        lambda m: f"Quiere {m.group(1).strip().rstrip('.')}.",
        0.8,
    ),
    # "X es mi proyecto/negocio/app"
    (
        re.compile(
            r"([A-ZÁÉÍÓÚÑ][\w\s]{1,30})\s+es\s+mi\s+"
            r"(?:proyecto|app|negocio|empresa|startup|producto|sistema|plataforma)",
            re.IGNORECASE | re.UNICODE,
        ),
        "work",
        lambda m: f"{m.group(0).strip().rstrip('.')}.",
        0.85,
    ),
    # Preferencias fuertes
    (
        re.compile(
            r"(?:(?:me\s+(?:gusta|encanta|apasiona)|prefiero|odio|detesto|"
            r"no\s+(?:me\s+)?gusta|i\s+(?:love|hate|prefer))\s+)(.{3,80})",
            re.IGNORECASE,
        ),
        "preference",
        lambda m: m.group(0).strip().rstrip(".") + ".",
        0.7,
    ),
    # Datos de ubicación / origen
    (
        re.compile(
            r"(?:vivo\s+en|soy\s+de|nací\s+en|i\s+live\s+in|i'?m\s+from)\s+"
            r"(.{3,60})",
            re.IGNORECASE,
        ),
        "identity",
        lambda m: m.group(0).strip().rstrip(".") + ".",
        0.85,
    ),
]


def extract_core(text: str) -> List[CoreExtraction]:
    """Extrae fragmentos importantes del texto para core_memory."""
    results = []
    for pattern, category, formatter, weight in _EXTRACTORS:
        m = pattern.search(text)
        if m:
            content = formatter(m)
            if content and len(content) >= 5:
                results.append(CoreExtraction(
                    category=category,
                    content=content,
                    weight=weight,
                ))
    return results


# ---------------------------------------------------------------------------
# Evaluador de importancia (scoring rápido sin LLM)
# ---------------------------------------------------------------------------

def _score_importance(text: str) -> float:
    """
    Puntúa la importancia general de un mensaje (0.0 = ruido, 1.0 = crítico).
    Se usa para decidir si absorber aunque no matchee un extractor específico.
    """
    score = 0.0
    t = text.lower()
    wc = max(len(t.split()), 1)

    # Señales de identidad y datos personales
    if any(w in t for w in ("me llamo", "mi nombre", "soy", "my name")):
        score += 0.4
    if any(w in t for w in ("trabajo", "negocio", "empresa", "profesión")):
        score += 0.3

    # Señales de proyecto / producto activo
    if any(w in t for w in ("vectrax", "mi proyecto", "mi sistema", "mi app", "mi producto")):
        score += 0.35
    if any(w in t for w in ("estoy construyendo", "estoy desarrollando", "estoy usando",
                            "estoy creando", "estoy lanzando")):
        score += 0.3

    # Señales de objetivos y preferencias
    if any(w in t for w in ("quiero", "necesito", "mi meta", "mi objetivo")):
        score += 0.25
    if any(w in t for w in ("me gusta", "prefiero", "me encanta", "odio", "no me gusta")):
        score += 0.2
    if any(w in t for w in ("publicar", "lanzar", "vender", "monetizar", "comercializar")):
        score += 0.25

    # Señales de opinión / creencia relevante
    if any(w in t for w in ("la gente", "los usuarios", "el problema", "la diferencia")):
        score += 0.15
    if re.search(r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+es\s+mi\s+", text):
        score += 0.3

    # Señales de baja importancia
    if wc <= 2:
        score -= 0.3
    if re.match(r"^(?:ok|sí|si|no|ya|vale|listo|gracias|hola|bye|\?)\b", t):
        score -= 0.5

    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# Almacenamiento — absorber en core
# ---------------------------------------------------------------------------

_ABSORB_THRESHOLD = 0.2  # Score mínimo para absorber (bajado de 0.3)


def absorb(user_id: str, user_input: str, bot_response: str = "") -> int:
    """
    Evalúa un input del usuario y absorbe lo importante en core_memory.

    Flujo:
      1. Extraer fragmentos explícitos (nombre, trabajo, relaciones, etc.)
      2. Si no hay fragmentos explícitos, evaluar importancia general
      3. Si pasa el umbral, almacenar como "general" con el texto raw
      4. Si ya existe algo similar, incrementar `times_confirmed` y weight

    Retorna: cantidad de entradas absorbidas.
    """
    if not user_id or not user_input or len(user_input.strip()) < 3:
        return 0

    text = user_input.strip()
    absorbed = 0
    now = time.time()

    # 1. Extraer fragmentos explícitos (nombre, trabajo, relaciones, etc.)
    extractions = extract_core(text)

    for ext in extractions:
        _upsert_core(user_id, ext.category, ext.content, ext.weight, now)
        absorbed += 1
        logger.info(
            "CORE absorbed | user=%s | cat=%s | w=%.2f | %s",
            user_id[:20], ext.category, ext.weight, ext.content[:60],
        )

    # 2. Fallback: si no hubo extracciones explícitas, evaluar importancia general
    if not extractions:
        score = _score_importance(text)
        # Absorber si el texto es relevante (umbral bajo para conversaciones naturales)
        if score >= _ABSORB_THRESHOLD and len(text.split()) >= 4:
            snippet = text[:200].rstrip(".")
            _upsert_core(user_id, "fact", snippet, round(score * 0.5, 2), now)
            absorbed += 1
            logger.debug(
                "CORE general absorbed | user=%s | score=%.2f | %s",
                user_id[:20], score, snippet[:60],
            )

    return absorbed


# Patrones que NO deben absorberse como hechos personales
_COMMAND_PATTERNS = re.compile(
    r"^(?:"
    # Imperativos directos
    r"dame|dime|busca|muestra|genera|escribe|crea|haz|explica|analiza|resume|"
    r"give me|show me|find|create|write|explain|tell me|"
    # Preguntas (no son hechos del usuario)
    r"c[oó]mo|de qu[eé]|para qu[eé]|qu[eé]|cu[aá]l|cu[aá]nto|cu[aá]ndo|"
    r"what|how|why|when|where|which|"
    # Solicitudes indirectas
    r"puedes|podr[ií]as|ser[ií]a posible|implementa|añade|mejora|"
    # Hablar de capacidades del sistema (no sobre el usuario)
    r"hablo de|habla de|en qu[eé] puede"
    r")",
    re.IGNORECASE,
)


def _is_command(text: str) -> bool:
    """True si el texto es una instrucción/pregunta, no un hecho personal."""
    return bool(_COMMAND_PATTERNS.match(text.strip()))


def _similar_content(a: str, b: str) -> bool:
    """
    True si dos contenidos son lo suficientemente similares para considerarse duplicados.
    Detecta:
    - Uno es substring del otro (normalizado)
    - Diferencia de 1-2 palabras (variantes menores)
    """
    a_norm = a.lower().strip().rstrip(".")
    b_norm = b.lower().strip().rstrip(".")
    if a_norm == b_norm:
        return True
    # Substring containment
    if a_norm in b_norm or b_norm in a_norm:
        return True
    # Diferencia mínima de palabras
    a_words = set(a_norm.split())
    b_words = set(b_norm.split())
    if len(a_words) > 2 and len(b_words) > 2:
        overlap = len(a_words & b_words) / max(len(a_words | b_words), 1)
        if overlap > 0.80:  # 80% de palabras en común
            return True
    return False


def _upsert_core(
    user_id: str,
    category: str,
    content: str,
    weight: float,
    now: float,
) -> None:
    """Inserta o actualiza una entrada en core_memory, evitando duplicados semánticos."""
    # No absorber comandos ni preguntas como hechos personales
    if _is_command(content):
        logger.debug("CORE skip (command): %s", content[:50])
        return

    try:
        conn = _conn()
        # Buscar similar: primero exacto, luego semántico
        existing_rows = conn.execute(
            "SELECT id, content, times_confirmed, weight FROM core_memory "
            "WHERE user_id = ? AND category = ?",
            (user_id, category),
        ).fetchall()

        existing = None
        for row in existing_rows:
            if _similar_content(row[1], content):
                existing = row
                break

        if existing:
            eid, old_content, times, old_weight = existing
            new_weight = min(1.0, old_weight + 0.05)
            # Guardar el contenido más informativo (más largo)
            better_content = content if len(content) > len(old_content) else old_content
            conn.execute(
                "UPDATE core_memory SET content = ?, times_confirmed = ?, weight = ?, updated_at = ? "
                "WHERE id = ?",
                (better_content, times + 1, new_weight, now, eid),
            )
            logger.debug("CORE reinforced | id=%d | times=%d | w=%.2f", eid, times + 1, new_weight)
        else:
            conn.execute(
                "INSERT INTO core_memory "
                "(user_id, category, content, weight, source, times_confirmed, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (user_id, category, content, weight, "conversation", now, now),
            )

        conn.commit()
        conn.close()
        # Invalidar caché de intención para este usuario
        try:
            from core.intention_projector import invalidate_cache
            invalidate_cache(user_id)
        except Exception:
            pass
    except Exception as exc:
        logger.error("Core upsert failed: %s", exc)


# ---------------------------------------------------------------------------
# Lectura — el alma del usuario
# ---------------------------------------------------------------------------

def get_soul(user_id: str) -> str:
    """
    Construye el contexto-alma de Vectrax para este usuario.

    Se inyecta PRIMERO en el prompt, antes de todo.
    Usa formato de instrucción para el LLM (no se muestra al usuario).
    """
    entries = get_core_entries(user_id)
    if not entries:
        return ""

    entries.sort(key=lambda e: e["weight"], reverse=True)

    by_cat: Dict[str, list] = {}
    for e in entries:
        cat = e["category"]
        if cat not in by_cat:
            by_cat[cat] = []
        by_cat[cat].append(e["content"])

    cat_order = ["identity", "work", "relationship", "goal", "preference", "habit", "emotion", "fact"]
    cat_labels = {
        "identity": "Quién es", "work": "Trabajo",
        "relationship": "Personas importantes", "goal": "Objetivos",
        "preference": "Preferencias", "habit": "Patrones",
        "emotion": "Estado", "fact": "Datos",
    }

    parts = ["[MEMORIA CENTRAL — Vectrax conoce a este usuario]"]
    for cat in cat_order:
        items = by_cat.get(cat)
        if not items:
            continue
        label = cat_labels.get(cat, cat)
        parts.append(f"• {label}: {' | '.join(items[:5])}")

    parts.append(
        "USA esta información de forma natural. "
        "No la listes. Responde COMO SI LO SUPIERAS DESDE SIEMPRE."
    )
    return "\n".join(parts)


# Max fragments per category for living response — prevents memory dumps
_LIVING_MAX_PER_CAT = {
    "identity": 2,
    "work": 1,
    "relationship": 2,
    "goal": 1,
    "preference": 1,
    "habit": 0,
    "emotion": 0,
    "fact": 0,
}
_LIVING_MAX_TOTAL = 6  # max sentences in the response


def _is_redundant(new: str, existing: List[str]) -> bool:
    """True if new fragment is a substring of or very similar to an existing one."""
    new_lower = new.lower().rstrip(".")
    for ex in existing:
        ex_lower = ex.lower().rstrip(".")
        # Substring check
        if new_lower in ex_lower or ex_lower in new_lower:
            return True
        # Word overlap > 70%
        new_words = set(new_lower.split())
        ex_words = set(ex_lower.split())
        if new_words and ex_words:
            overlap = len(new_words & ex_words) / max(len(new_words), len(ex_words))
            if overlap > 0.7:
                return True
    return False


def get_living_response(user_id: str, lang: str = "es") -> str:
    """
    Genera una respuesta VIVA desde la memoria central.

    Se usa cuando el usuario pregunta:
      - ¿me conoces?
      - ¿qué sabes de mí?
      - ¿quién soy para ti?

    Reglas:
      - Máximo ~5-6 oraciones limpias, no una lista.
      - Solo las entradas de mayor peso por categoría.
      - Sin duplicados ni fragmentos contradictorios.
      - Habla como si lo supieras desde siempre.

    Ejemplo:
      "Sí. Estás dentro de mi memoria central.
       Eres Mario. Vives en Miami. Trabajas como el creador de Vectrax.
       Ana es tu socia. Eso ya no se pierde."
    """
    entries = get_core_entries(user_id)
    if not entries:
        if lang == "es":
            return (
                "Aún no tengo nada en mi memoria central sobre ti. "
                "Dime quién eres, qué haces, qué necesitas — y lo absorbo."
            )
        return (
            "I don't have anything in my core memory about you yet. "
            "Tell me who you are, what you do, what you need — and I'll absorb it."
        )

    # Already sorted by weight DESC from get_core_entries
    by_cat: Dict[str, list] = {}
    for e in entries:
        cat = e["category"]
        if cat not in by_cat:
            by_cat[cat] = []
        by_cat[cat].append(e["content"])

    fragments: List[str] = []

    # ── Identity: name first, then location/origin (max 2) ──
    identity = by_cat.get("identity", [])
    name = ""
    for item in identity:
        if "llama" in item.lower():
            name = item.replace("Se llama ", "").rstrip(".")
            break
    if name:
        fragments.append(f"Eres {name}." if lang == "es" else f"You are {name}.")

    id_count = 1 if name else 0
    id_max = _LIVING_MAX_PER_CAT.get("identity", 2)
    for item in identity:
        if id_count >= id_max:
            break
        if "llama" in item.lower():
            continue
        if _is_redundant(item, fragments):
            continue
        fragments.append(item)
        id_count += 1

    # ── Work: only highest-weight (max 1) ──
    work_max = _LIVING_MAX_PER_CAT.get("work", 1)
    work_count = 0
    for item in by_cat.get("work", []):
        if work_count >= work_max:
            break
        txt = item.replace("Trabaja como", "Trabajas como")
        txt = txt.replace("Trabaja en/como", "Trabajas como")
        if _is_redundant(txt, fragments):
            continue
        fragments.append(txt)
        work_count += 1

    # ── Relationships: max 2 ──
    rel_max = _LIVING_MAX_PER_CAT.get("relationship", 2)
    rel_count = 0
    for item in by_cat.get("relationship", []):
        if rel_count >= rel_max:
            break
        txt = item.replace(" es su ", " es tu ")
        if _is_redundant(txt, fragments):
            continue
        fragments.append(txt)
        rel_count += 1

    # ── Goals: max 1 ──
    goal_max = _LIVING_MAX_PER_CAT.get("goal", 1)
    goal_count = 0
    for item in by_cat.get("goal", []):
        if goal_count >= goal_max:
            break
        txt = item.replace("Quiere ", "Quieres ")
        if _is_redundant(txt, fragments):
            continue
        fragments.append(txt)
        goal_count += 1

    # ── Preferences: max 1 ──
    pref_max = _LIVING_MAX_PER_CAT.get("preference", 1)
    pref_count = 0
    for item in by_cat.get("preference", []):
        if pref_count >= pref_max:
            break
        if _is_redundant(item, fragments):
            continue
        fragments.append(item)
        pref_count += 1

    if not fragments:
        return ""

    # Hard cap on total fragments
    fragments = fragments[:_LIVING_MAX_TOTAL]

    body = " ".join(fragments)

    if lang == "es":
        return f"Sí. Estás dentro de mi memoria central. {body} Eso ya no se pierde."
    return f"Yes. You're in my core memory. {body} That's permanent now."


def get_core_entries(user_id: str) -> List[dict]:
    """Retorna todas las entradas de core_memory para el usuario."""
    try:
        conn = _conn()
        rows = conn.execute(
            "SELECT id, category, content, weight, times_confirmed, created_at "
            "FROM core_memory WHERE user_id = ? "
            "ORDER BY weight DESC, updated_at DESC",
            (user_id,),
        ).fetchall()
        conn.close()
        return [
            {
                "id": r[0], "category": r[1], "content": r[2],
                "weight": r[3], "times_confirmed": r[4], "created_at": r[5],
            }
            for r in rows
        ]
    except Exception:
        return []


def get_core_stats(user_id: str) -> Dict:
    """Estadísticas de core_memory para un usuario."""
    entries = get_core_entries(user_id)
    if not entries:
        return {"entries": 0, "categories": {}}
    cats = {}
    for e in entries:
        cats[e["category"]] = cats.get(e["category"], 0) + 1
    return {
        "entries": len(entries),
        "categories": cats,
        "top_weight": max(e["weight"] for e in entries),
        "avg_weight": sum(e["weight"] for e in entries) / len(entries),
    }
