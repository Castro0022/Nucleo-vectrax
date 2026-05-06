"""
core/voice/visual_humanizer.py — Visual Humanizer.

Convierte descripciones crudas del modelo de visión en texto humano,
breve y conversacional. Parte del Motor de Voz Viva: lo que sale al
usuario suena a Vectrax mirando, no a un reporte forense.

Reglas duras (cumplidas por tests):
  - Evitar lenguaje de reporte ("se observa", "se aprecia").
  - Evitar aperturas tipo "la imagen muestra", "en la foto", "I can see".
  - Evitar descripción exhaustiva (cada objeto, cada color, cada esquina).
  - Priorizar percepción humana (lo más cargado emocionalmente o lo
    más "vivo" — no la metadata).
  - Máximo 4 frases.
  - Tono natural y conversacional.
  - Si reconoce personas frecuentes (faces=["Mario","Naomy"]),
    responder con continuidad contextual ("Mario y Naomy otra vez")
    en vez de descripción anónima.

API pública:
    humanize_visual(raw, faces=None, user_name="", lang="es") -> str

`raw` es la salida cruda del modelo de visión.
`faces` es la lista de nombres reconocidos por face_memory (puede
incluir el propio nombre del usuario si tiene su cara registrada).
`user_name` es el nombre del usuario que envió la foto (para distinguir
"tú" de "ellos").
`lang` ajusta plantillas de continuidad ("es"|"en").

Ejemplo:
    raw = ("La imagen muestra a dos personas sonriendo. "
           "En el fondo se observa una playa. La luz es cálida. "
           "Hay palmeras y arena dorada. Las personas parecen felices.")
    faces = ["Mario", "Naomy"]
    user_name = "Mario"
    →
    "Tú y Naomy en la playa. Se les ve bien."
"""

from __future__ import annotations

import re
from typing import List, Optional


MAX_SENTENCES = 4

# Aperturas a remover (lenguaje de reporte / encyclopédico).
# Cubre tanto inicio absoluto como prefijos espaciales ("En el fondo
# se observa", "Al fondo se aprecia", "A lo lejos se ve").
_REPORT_OPENERS = re.compile(
    r"^\s*(?:"
    # "La imagen muestra", "En la foto se observa", etc.
    r"(?:la|esta|en\s+(?:la|esta))\s+(?:imagen|foto|fotograf[ií]a|escena)\s+"
    r"(?:muestra|presenta|contiene|representa|describe|captur[aó])[\s,:]*"
    r"|(?:en\s+)?(?:la\s+)?(?:imagen|foto)[\s,:]*"
    # Prefijos espaciales + verbo pasivo de reporte
    r"|(?:en|al|a)\s+(?:el\s+|la\s+|los\s+|las\s+|lo\s+)?"
    r"(?:fondo|frente|lateral|lado|costado|primer\s+plano|segundo\s+plano|centro|esquina|lejos|cerca)\s*,?\s+"
    r"se\s+(?:observa|aprecia|ve|puede\s+ver|nota|distingue|encuentra|muestra)[\s,:]*"
    # Verbo pasivo solo (sin prefijo espacial)
    r"|se\s+(?:observa|aprecia|ve|puede\s+ver|nota|distingue)[\s,:]*"
    # Generic Spanish openers
    r"|(?:aqu[ií]|all[ií])\s+(?:hay|tenemos|vemos)[\s,:]*"
    # English equivalents
    r"|the\s+(?:image|photo|picture|photograph|scene)\s+"
    r"(?:shows|depicts|presents|contains|features|captures)[\s,:]*"
    r"|(?:in\s+)?(?:the\s+)?(?:image|photo|picture)[\s,:]*"
    r"|in\s+the\s+(?:background|foreground|distance|center)\s*,?\s+"
    r"(?:we\s+can\s+see|you\s+can\s+see|there\s+(?:is|are))[\s,:]*"
    r"|i\s+(?:can\s+)?see[\s,:]*"
    r"|there\s+(?:is|are)[\s,:]*"
    r")",
    re.IGNORECASE,
)

# Conectores de descripción exhaustiva — los recortamos cuando aparecen
# al inicio de oraciones internas para compactar.
_REPORT_INTERNAL = re.compile(
    r"\b(?:adem[aá]s\s+(?:se\s+(?:observa|aprecia|ve)))",
    re.IGNORECASE,
)

# Marcadores estructurales que parecen viñetas / listas (eliminamos)
_BULLETS = re.compile(r"^[\s•\-\*]+", re.MULTILINE)


def humanize_visual(
    raw: str,
    faces: Optional[List[str]] = None,
    user_name: str = "",
    lang: str = "es",
) -> str:
    """Devuelve la descripción humanizada lista para enviar al usuario.

    Nunca levanta excepción; si raw está vacío devuelve cadena vacía y el
    caller decide qué hacer.
    """
    if not raw or not raw.strip():
        return ""

    text = raw.strip()

    # 1. Eliminar bullets antes que nada
    text = _BULLETS.sub("", text)

    # 2. Quitar aperturas de reporte: por cada oración. Así atrapamos
    #    "En el fondo se observa..." en medio del texto, no solo al
    #    inicio.
    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    cleaned_sentences = []
    for s in sentences:
        cur = s
        for _ in range(3):
            new = _REPORT_OPENERS.sub("", cur).lstrip(" ,;:.")
            if new == cur:
                break
            cur = new
        cur = cur.strip()
        # Si lo que queda no aporta sustancia (<4 chars), descartamos
        if len(cur) >= 4:
            # Capitalizar primera letra de cada oración
            if cur[0].islower():
                cur = cur[0].upper() + cur[1:]
            cleaned_sentences.append(cur)
    text = " ".join(cleaned_sentences)

    # 3. Recortar conectores internos de reporte
    text = _REPORT_INTERNAL.sub("Y", text)

    # 4. Limitar a MAX_SENTENCES oraciones
    text = _limit_sentences(text, MAX_SENTENCES)

    # 5. Limpiar espacios y puntuación
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r" +([.,;:!?])", r"\1", text)
    text = text.strip()

    # 6. Capitalizar primera letra si se perdió por strip de opener
    if text and text[0].islower():
        text = text[0].upper() + text[1:]

    # 7. Continuidad contextual si hay rostros reconocidos
    if faces:
        prefix = _continuity_prefix(faces, user_name, lang)
        if prefix and not _starts_with_names(text, faces, user_name):
            text = f"{prefix} {text}".strip()

    # 8. Sustancia mínima
    if len(text.strip()) < 4:
        return ""

    return text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _limit_sentences(text: str, max_sentences: int) -> str:
    """Trunca a las primeras N oraciones, preservando puntuación."""
    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    if len(sentences) <= max_sentences:
        return " ".join(sentences)
    return " ".join(sentences[:max_sentences])


def _continuity_prefix(
    faces: List[str],
    user_name: str,
    lang: str,
) -> str:
    """Frase corta de reconocimiento que da continuidad humana.

    Variantes según composición:
      - solo el user (Mario)         → ""  (no hace falta)
      - user + 1 otro (Mario, Naomy) → "Tú y Naomy."
      - user + varios (Mario, A, B)  → "Tú con A y B."
      - solo otros (Naomy)           → "Naomy."
      - varios otros                 → "A y B."
    """
    if not faces:
        return ""

    # Quitar duplicados preservando orden
    seen = set()
    clean = [f for f in faces if not (f in seen or seen.add(f))]

    user_in = bool(user_name) and any(_match_name(user_name, f) for f in clean)
    others = [f for f in clean if not _match_name(user_name, f)] if user_name else clean

    if lang == "en":
        you = "You"
        with_word = "with"
        and_word = "and"
    else:
        you = "Tú"
        with_word = "con"
        and_word = "y"

    if user_in and not others:
        # Solo el creador en la foto — no agregamos prefijo
        return ""

    if user_in and len(others) == 1:
        return f"{you} {and_word} {others[0]}."

    if user_in and len(others) > 1:
        joined = _join_humanly(others, and_word)
        return f"{you} {with_word} {joined}."

    if not user_in and len(clean) == 1:
        return f"{clean[0]}."

    if not user_in and len(clean) > 1:
        joined = _join_humanly(clean, and_word)
        return f"{joined}."

    return ""


def _join_humanly(items: List[str], and_word: str = "y") -> str:
    """['A','B','C'] → 'A, B y C'  /  ['A','B'] → 'A y B'  /  ['A'] → 'A'."""
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} {and_word} {items[1]}"
    return ", ".join(items[:-1]) + f" {and_word} {items[-1]}"


def _match_name(a: str, b: str) -> bool:
    """Comparación tolerante de nombres (case-insensitive, primer token)."""
    if not a or not b:
        return False
    a0 = a.strip().split()[0].lower()
    b0 = b.strip().split()[0].lower()
    return a0 == b0


def _starts_with_names(text: str, faces: List[str], user_name: str) -> bool:
    """True si el texto ya empieza mencionando a alguno de los nombres
    (entonces no agregamos prefijo redundante)."""
    if not text:
        return False
    head = text[:80].lower()
    candidates = list(faces or [])
    if user_name:
        candidates.append(user_name)
    for n in candidates:
        if not n:
            continue
        first = n.split()[0].lower()
        if first and first in head:
            return True
    return False
