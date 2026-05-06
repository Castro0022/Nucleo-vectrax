"""
core/voice/anti_repetition.py — Anti-Repetition Filter.

Cierra estructuralmente la clase de bug "Vectrax repite los mismos
clichés / frases / estructuras". Mantiene, por usuario, una ventana
deslizante con las últimas N respuestas enviadas. Antes de mandar una
respuesta candidata:

  1. is_too_similar(user_id, candidate)  →  True si supera el umbral
                                            de similitud con cualquiera
                                            de las N más recientes.
  2. strip_cliches(text)                 →  drop de frases prohibidas
                                            (publicar/redes sociales,
                                            community manager, etc.).
  3. perturb_structure(candidate, ...)   →  reescritura estructural
                                            mínima cuando hay
                                            similitud demasiado alta.
  4. record_response(user_id, text)      →  graba la salida final.

API pública:
    is_too_similar(user_id, candidate, threshold=0.55) -> bool
    strip_cliches(text, lang="es") -> str
    perturb_structure(candidate, user_id, lang="es") -> str
    record_response(user_id, text) -> None
    recent_responses(user_id, n=10) -> list[str]
    clear_for_user(user_id) -> None

Backend: SQLite en `~/.vectrax/anti_repetition.db`. Append-only por
usuario; selección por timestamp DESC; pruning automático cuando un
usuario supera N entries × factor.

Diseñado para millones de users:
  - Índice por user_id.
  - Pruning O(1) amortizado.
  - Sin estado en proceso (sobrevive reinicios del worker).

Creador: Mario Bravo Castro
Fecha:   2026-05-06
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from typing import List, Optional

logger = logging.getLogger("vectrax.voice.anti_repetition")


# ===========================================================================
# Configuración
# ===========================================================================

WINDOW_SIZE = 10                 # cuántas últimas respuestas por user
PRUNE_FACTOR = 3                 # mantener hasta N*PRUNE_FACTOR antes de podar
SIMILARITY_THRESHOLD = 0.55      # Jaccard sobre tri-gramas de palabras
NGRAM_N = 3

_DB_PATH = os.environ.get(
    "VECTRAX_ANTIREP_DB",
    os.path.join(
        os.path.expanduser("~"),
        ".vectrax", "anti_repetition.db",
    ),
)

_CREATE = """
CREATE TABLE IF NOT EXISTS responses (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   TEXT NOT NULL,
    text      TEXT NOT NULL,
    ts        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_user_ts ON responses(user_id, ts DESC);
"""

_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    c = sqlite3.connect(_DB_PATH, timeout=3)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_CREATE)
    return c


# ===========================================================================
# Cliché stripper
# ===========================================================================

# Frases/conceptos prohibidos. Cualquier ORACIÓN que matchee uno de
# estos patrones se descarta entera. Defensa en profundidad: aunque
# el LLM ignore el system prompt, la salida no llega al usuario.
_CLICHE_PATTERNS = [
    # Sugerencias de "publicar / subir / compartir" en redes sociales
    re.compile(
        r"(?:"
        # Verbos infinitivo + clítico (subirla, compartirla, etc.)
        r"(?:subir(?:la|las|lo|los)?|publicar(?:la|las|lo|los)?"
        r"|compartir(?:la|las|lo|los)?|postear(?:la|las|lo|los)?"
        r"|colgar(?:la|las|lo|los)?)\s+"
        r"(?:[^.!?]{0,40}\s+)?"
        r"(?:redes\s+sociales|instagram|facebook|tiktok|twitter|x"
        r"|stor(?:y|ies)|reel(?:s)?|feed|tu(?:s)?\s+muro|tu(?:s)?\s+perfil)"
        # Imperativos (compártela, súbela, pósteala, cólgala, publícala)
        r"|(?:comp[aá]rt|s[uú]b|p[oó]ste|c[oó]lg|publ[ií]c)(?:e|a)la(?:s)?\b"
        r"|(?:comp[aá]rt|s[uú]b|p[oó]ste|c[oó]lg|publ[ií]c)(?:e|a)lo(?:s)?\b"
        # Conector hacia un destino social (para/en tus stories/feed/...)
        r"|(?:para|en|a)\s+(?:tu(?:s)?\s+)?"
        r"(?:instagram|facebook|tiktok|stor(?:y|ies)|reel(?:s)?|feed|redes(?:\s+sociales)?)"
        # Calificativo seguido de destino social
        r"|(?:bueno|buena|perfecta|ideal|excelente)\s+(?:para|de)\s+"
        r"(?:tu(?:s)?|las|el|la)?\s*"
        r"(?:redes(?:\s+sociales)?|instagram|stor(?:y|ies)|reel(?:s)?|feed|tiktok|facebook)"
        # "sería gran foto para..." pattern
        r"|(?:ser[ií]a|es)\s+(?:un|una)?\s*(?:gran|buena|excelente|perfecta|ideal)\s+"
        r"(?:foto|imagen|toma|momento)\s+(?:para|de)\s+"
        r"(?:tu(?:s)?|las|el|la)?\s*"
        r"(?:redes(?:\s+sociales)?|instagram|stor(?:y|ies)|reel(?:s)?|feed|tiktok|facebook)"
        r")",
        re.IGNORECASE,
    ),
    # Inglés equivalente
    re.compile(
        r"(?:"
        r"(?:post(?:ing)?|share|sharing|upload(?:ing)?)\s+"
        r"(?:this|it|the\s+(?:photo|picture|image))?\s*"
        r"(?:on|to)\s+(?:social\s+media|instagram|facebook|tiktok|twitter|x|stories|reels|your\s+feed)"
        r"|(?:great|perfect|ideal|good)\s+for\s+(?:your\s+)?(?:instagram|stories|reels|feed|social(?:\s+media)?)"
        r"|(?:would\s+make|makes)\s+a\s+(?:great|good|nice)\s+(?:instagram|story|reel|post)"
        r")",
        re.IGNORECASE,
    ),
]

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def strip_cliches(text: str, lang: str = "es") -> str:
    """Elimina oraciones que contengan clichés prohibidos.

    Si la oración entera matchea un cliché, se descarta. Si solo una
    parte matchea, también descartamos la oración completa (preferimos
    silencio antes que un cliché parcial). Si el texto queda vacío
    tras la limpieza, devolvemos cadena vacía y el caller decide.
    """
    if not text or not text.strip():
        return ""
    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    kept: List[str] = []
    for s in sentences:
        is_cliche = any(p.search(s) for p in _CLICHE_PATTERNS)
        if is_cliche:
            logger.info("anti_repetition: dropped cliche sentence: %r", s[:120])
            continue
        kept.append(s)
    return " ".join(kept).strip()


# ===========================================================================
# Similitud (Jaccard sobre n-gramas de palabras)
# ===========================================================================

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _ngrams(tokens: List[str], n: int = NGRAM_N) -> set:
    if len(tokens) < n:
        return set(tuple(tokens),) if tokens else set()
    return {tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def similarity(a: str, b: str) -> float:
    """Similitud Jaccard entre dos textos sobre tri-gramas de palabras.

    Devuelve 0.0..1.0. 1.0 = idénticos. >0.55 ya percibido como repetido.
    """
    return _jaccard(_ngrams(_tokens(a)), _ngrams(_tokens(b)))


# ===========================================================================
# Ring buffer per-user en SQLite
# ===========================================================================

def record_response(user_id: str, text: str) -> None:
    """Graba la respuesta final enviada al user. Nunca raise."""
    if not user_id or not text or not text.strip():
        return
    try:
        with _lock:
            c = _conn()
            c.execute(
                "INSERT INTO responses (user_id, text, ts) VALUES (?, ?, ?)",
                (user_id, text.strip(), time.time()),
            )
            c.commit()
            # Pruning: si el user tiene más de N*PRUNE_FACTOR entries,
            # borra todo lo más viejo dejando exactamente WINDOW_SIZE.
            count_row = c.execute(
                "SELECT COUNT(*) FROM responses WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if count_row and count_row[0] > WINDOW_SIZE * PRUNE_FACTOR:
                c.execute(
                    "DELETE FROM responses WHERE user_id = ? AND id NOT IN ("
                    "  SELECT id FROM responses WHERE user_id = ? "
                    "  ORDER BY ts DESC LIMIT ?"
                    ")",
                    (user_id, user_id, WINDOW_SIZE),
                )
                c.commit()
            c.close()
    except Exception as exc:
        logger.debug("record_response failed: %s", exc)


def recent_responses(user_id: str, n: int = WINDOW_SIZE) -> List[str]:
    """Devuelve las últimas n respuestas del user, más reciente primero."""
    if not user_id:
        return []
    try:
        with _lock:
            c = _conn()
            rows = c.execute(
                "SELECT text FROM responses WHERE user_id = ? "
                "ORDER BY ts DESC LIMIT ?",
                (user_id, n),
            ).fetchall()
            c.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def is_too_similar(
    user_id: str,
    candidate: str,
    threshold: float = SIMILARITY_THRESHOLD,
    n: int = WINDOW_SIZE,
) -> bool:
    """True si `candidate` es demasiado parecido a alguna de las últimas
    n respuestas del user (Jaccard ≥ threshold)."""
    if not candidate or not candidate.strip():
        return False
    recents = recent_responses(user_id, n=n)
    cand_ng = _ngrams(_tokens(candidate))
    for r in recents:
        if _jaccard(cand_ng, _ngrams(_tokens(r))) >= threshold:
            return True
    return False


def clear_for_user(user_id: str) -> int:
    """Borra todo el historial del user. Devuelve cuántas filas borró."""
    if not user_id:
        return 0
    try:
        with _lock:
            c = _conn()
            cur = c.execute(
                "DELETE FROM responses WHERE user_id = ?", (user_id,),
            )
            c.commit()
            n = cur.rowcount
            c.close()
        return n
    except Exception:
        return 0


def reset_for_tests() -> None:
    """Test-only: clear all rows."""
    try:
        with _lock:
            c = _conn()
            c.execute("DELETE FROM responses")
            c.commit()
            c.close()
    except Exception:
        pass


# ===========================================================================
# Perturbación estructural
# ===========================================================================

# Pares de reescritura para forzar variedad cuando detectamos repetición.
# Estos son cambios MÍNIMOS, conservadores: alteran orden y aperturas
# pero nunca cambian el significado.
_PERTURB_OPENERS = [
    (re.compile(r"^Sales\s+genial\s+en\s+esa\s+foto,\s+(\w+)\.\s*", re.IGNORECASE),
     lambda m: f"{m.group(1)}, te ves bien en esa. "),
    (re.compile(r"^Te\s+ves\s+bien,\s+(\w+)\.\s*", re.IGNORECASE),
     lambda m: f"Buena foto, {m.group(1)}. "),
    (re.compile(r"^Buena\s+foto,\s+(\w+)\.\s*", re.IGNORECASE),
     lambda m: f"Qué buen momento, {m.group(1)}. "),
    (re.compile(r"^Qué\s+buen\s+momento,\s+(\w+)\.\s*", re.IGNORECASE),
     lambda m: f"Sales genial en esa foto, {m.group(1)}. "),
    # English
    (re.compile(r"^You\s+look\s+great\s+in\s+that\s+one,\s+(\w+)\.\s*", re.IGNORECASE),
     lambda m: f"Nice shot of you, {m.group(1)}. "),
    (re.compile(r"^Nice\s+shot\s+of\s+you,\s+(\w+)\.\s*", re.IGNORECASE),
     lambda m: f"Good moment, {m.group(1)}. "),
]


def perturb_structure(candidate: str, user_id: str, lang: str = "es") -> str:
    """Cuando el candidato es demasiado similar a algo reciente, lo
    perturbamos estructuralmente sin cambiar el significado:
      - Rota el opener si es uno conocido.
      - Si no encuentra un opener conocido, intenta partir y reordenar
        las dos primeras oraciones.
    Si tras la perturbación sigue siendo similar, devuelve el candidato
    original (mejor algo a nada).
    """
    if not candidate:
        return candidate

    # 1. Intento de rotación de opener
    rotated = candidate
    for pat, rep in _PERTURB_OPENERS:
        new = pat.sub(rep, rotated, count=1)
        if new != rotated:
            rotated = new
            break

    if rotated != candidate and not is_too_similar(user_id, rotated):
        return rotated.strip()

    # 2. Reordenamiento de las dos primeras oraciones
    sents = [s.strip() for s in _SENT_SPLIT.split(candidate) if s.strip()]
    if len(sents) >= 2:
        swapped = " ".join([sents[1], sents[0]] + sents[2:])
        if not is_too_similar(user_id, swapped):
            return swapped.strip()

    # 3. Si nada ayuda, devuelve original
    return candidate


# ===========================================================================
# Filtro componible end-to-end
# ===========================================================================

def filter_response(
    candidate: str,
    user_id: str,
    lang: str = "es",
) -> Optional[str]:
    """Pipeline completo de filtrado:

      1. strip_cliches
      2. is_too_similar → perturb_structure si aplica
      3. Si tras todo sigue muy similar Y es muy corto → devolver None
         (caller debe regenerar). Si es razonable, devolver lo que haya.

    Devuelve el texto limpio listo para enviar, o None si la respuesta
    no pasa el gate y debe regenerarse.
    """
    if not candidate or not candidate.strip():
        return None

    # 1. Cliché stripper
    cleaned = strip_cliches(candidate, lang=lang)
    if not cleaned:
        # Todo era cliché → null para que el caller regenere.
        return None

    # 2. Si el cleaned es muy similar a algo reciente, perturbamos
    if is_too_similar(user_id, cleaned):
        perturbed = perturb_structure(cleaned, user_id, lang=lang)
        if perturbed and not is_too_similar(user_id, perturbed):
            return perturbed.strip()
        # 3. Si la perturbación no bastó y el texto es muy corto,
        #    pedimos regeneración. Si es razonablemente largo, dejamos
        #    pasar el original (mejor algo a nada).
        if len(cleaned) < 60:
            return None
        return cleaned.strip()

    return cleaned.strip()
