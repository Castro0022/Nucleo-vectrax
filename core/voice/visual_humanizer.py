"""
core/voice/visual_humanizer.py — Visual Humanizer.

Convierte descripciones crudas del modelo de visión en texto humano,
breve y conversacional. Parte del Motor de Voz Viva: lo que sale al
usuario suena a Vectrax mirando, no a un reporte forense.

Filosofía: una foto es una INTERACCIÓN, no una descripción. Vectrax
está viendo la foto contigo, no analizándola desde afuera. Por eso:
  - Si el usuario aparece en la foto, le hablamos de tú.
  - Si lo reconocemos solo, le hablamos directamente con un opener
    de compañero ("Sales genial en esa foto, Mario.").
  - Las menciones en tercera persona del usuario ("a Mario y otra
    persona", "Mario sonríe") se reescriben a segunda persona.
  - El relleno atmosférico forense ("el clima es soleado", "el día
    está despejado", "hace buen tiempo") se descarta: no aporta a la
    interacción humana.

Reglas duras (cumplidas por tests):
  - Evitar lenguaje de reporte ("se observa", "se aprecia").
  - Evitar aperturas tipo "la imagen muestra", "en la foto", "I can see".
  - Evitar descripción exhaustiva (cada objeto, cada color, cada esquina).
  - Priorizar percepción humana (lo más cargado emocionalmente o lo
    más "vivo" — no la metadata).
  - Prohibir tercera persona distante sobre el usuario reconocido.
  - Máximo 4 frases.
  - Tono natural y conversacional, de compañero mirando contigo.
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

Ejemplos:
    raw = ("La imagen muestra a dos personas sonriendo. "
           "En el fondo se observa una playa. La luz es cálida. "
           "Hay palmeras y arena dorada. Las personas parecen felices.")
    faces = ["Mario", "Naomy"]
    user_name = "Mario"
    →
    "Tú y Naomy en la playa. Se les ve bien."

    raw = "A Mario y otra persona en una terraza. El clima es soleado."
    faces = ["Mario"]
    user_name = "Mario"
    →
    "Sales genial en esa foto, Mario. Tú y alguien en una terraza."
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

# Oraciones "atmosféricas" puras (clima/escenario sin gente). Las
# dropeamos porque son lenguaje de reporte forense, no interacción.
# Solo se considera atmosférica una oración cuyo CUERPO COMPLETO
# matchea el patrón.
_ATMOSPHERIC_FULL = re.compile(
    r"^\s*(?:"
    # Español
    r"(?:el|la)\s+(?:clima|d[ií]a|sol|cielo|tiempo|temperatura|luz|horizonte|atm[oó]sfera|ambiente|escenario|paisaje|fondo)"
    r"\s+(?:es|est[aá]|se\s+ve|parece)\s+[^.!?]{2,80}"
    r"|hace\s+(?:buen|mal|mucho|bastante)\s+(?:tiempo|sol|calor|fr[ií]o|d[ií]a)[^.!?]{0,40}"
    r"|hay\s+(?:un|una)?\s*(?:cielo|sol|atardecer|amanecer|nubes|nieve|lluvia)[^.!?]{0,40}"
    # Inglés
    r"|the\s+(?:weather|sky|sun|light|day|atmosphere|background|scenery)"
    r"\s+(?:is|looks|seems|appears)\s+[^.!?]{2,80}"
    r"|it\s+(?:is|looks|seems)\s+(?:a\s+)?(?:sunny|cloudy|rainy|nice|warm|cold|bright|dark)[^.!?]{0,40}"
    r")\s*[.!?]?\s*$",
    re.IGNORECASE,
)

# Companion openers — cuando reconozco al usuario en la foto le hablo
# directamente, como un compañero mirando la foto con él. Pool pequeño
# para variabilidad sin parecer aleatorio. Selección determinista por
# hash del raw input para tests estables.
_COMPANION_OPENERS = {
    "es": [
        "Sales genial en esa foto, {name}.",
        "Te ves bien, {name}.",
        "Buena foto, {name}.",
        "Qué buen momento, {name}.",
    ],
    "en": [
        "You look great in that one, {name}.",
        "Nice shot of you, {name}.",
        "Good moment, {name}.",
        "Looking sharp, {name}.",
    ],
}

# Sustantivos genéricos (singular) que designan a la persona en la foto.
# Cuando el user está RECONOCIDO Y SOLO en la foto, los reescribimos a
# segunda persona ("una persona sonriendo" → "sonriendo", o lo absorbe
# el companion opener).
_GENERIC_SUBJECT_ES = re.compile(
    r"\b(?:una|la|un|el)\s+(?:persona|hombre|mujer|chico|chica|tipo|se\u00f1or|se\u00f1ora|joven|individuo|sujeto)\b",
    re.IGNORECASE,
)
_GENERIC_SUBJECT_EN = re.compile(
    r"\b(?:a|the|one)\s+(?:person|man|woman|guy|girl|individual|figure)\b",
    re.IGNORECASE,
)

# Verbos descriptivos de estado emocional pasivo. Cualquier oración que
# matchee se descarta entera. Vectrax es socio, no terapeuta: hablamos
# CON el user, no DE él ni de las personas de la foto.
_PASSIVE_DESCRIPTOR_PATTERNS = [
    re.compile(
        r"(?:"
        # "parecen / parece / se ven / se nota / se aprecia" + estado emocional
        r"\b(?:parec[ea]n?|se\s+ven?|se\s+nota|se\s+aprecia|se\s+percibe|transmit(?:e|en)|emana[ns]?)\s+"
        r"(?:[^.!?]{0,30}\s+)?"
        r"(?:disfrut\w+|relajad\w+|felic\w+|content\w+|c[oó]mod\w+|tranquil\w+|emocionad\w+|alegr\w+|paz|armon[ií]a|serenidad|calma|nostalgia|melancol[ií]a)"
        # "dan / dar / aporta / aportan un toque/aire/ambiente <adj>"
        r"|\b(?:dan|da|dar|aporta[ns]?)\s+(?:un|una)\s+(?:toque|aire|ambiente|vibra)\s+\w+"
        # "parece / sería una (buena|gran|excelente|perfecta|ideal) (ocasión|oportunidad|forma|manera) (para|de) ..."
        r"|\b(?:parece|ser[ií]a)\s+(?:una|un)?\s*(?:buena|gran|excelente|perfecta|ideal|linda|hermosa)\s+"
        r"(?:ocasi[oó]n|oportunidad|forma|manera|momento|d[ií]a|tarde|noche)\s+(?:para|de)\b"
        # "se respira / se siente <emocion>"
        r"|\bse\s+(?:respira|siente)\s+(?:[^.!?]{0,30}\s+)?(?:paz|tranquilidad|calma|alegr[ií]a|amor|armon[ií]a)\b"
        r")",
        re.IGNORECASE,
    ),
    # Inglés
    re.compile(
        r"(?:"
        # "seem/seems/look/looks to enjoy/be/have ..." (verb phrase)
        r"\b(?:they|you|he|she|it)?\s*(?:seem|seems|look|looks|appear|appears)\s+"
        r"to\s+(?:enjoy|be|have|love|find|share)\b"
        # "seem/look + adjective de estado emocional"
        r"|\b(?:they|you|he|she|it)?\s*(?:seem|seems|look|looks|appear|appears)\s+"
        r"(?:so|very|really|quite|pretty)?\s*"
        r"(?:enjoying|relaxed|happy|content|comfortable|calm|peaceful|joyful|excited|nostalgic|chill|cozy)"
        r"|\b(?:gives|adds|brings)\s+(?:a|the)\s+(?:relaxed|chill|warm|nice|cool|cozy)\s+(?:touch|vibe|feel|atmosphere)"
        r"|\b(?:looks|seems)\s+like\s+(?:a|the)?\s*(?:good|great|nice|perfect|ideal)\s+(?:occasion|opportunity|moment|time|day|spot)\s+(?:to|for)\b"
        r"|\b(?:radiates|exudes)\s+\w+"
        r")",
        re.IGNORECASE,
    ),
]

# Pool de cierres curiosos (preguntas) cuando la respuesta no contiene
# ninguna interrogante. Mario reporta tono pasivo — cada salida debe
# cerrar con una pregunta especifica que lo invite a continuar.
_CURIOSITY_QUESTIONS = {
    "es": [
        "¿Dónde fue?",
        "Cuéntame, ¿qué vibra había?",
        "¿Eso al fondo es nuevo o ya estaba?",
        "¿Quién más estaba ese día?",
        "Oye, ¿qué ocasión era?",
        "¿Cómo llegó ese momento?",
        "¿Qué te llevó hasta ahí?",
    ],
    "en": [
        "Where was that?",
        "Tell me, what was the vibe?",
        "Is that new in the background?",
        "Who else was around?",
        "What was the occasion?",
        "How did that moment come together?",
        "What got you there?",
    ],
}

# Marcadores de cierre activo aceptables (alternativa a la pregunta):
# si la respuesta contiene una de estas frases, NO se agrega pregunta.
_ACTIVE_CLOSURE_MARKERS = re.compile(
    r"(?:"
    r"me\s+da\s+curiosidad|me\s+mata\s+la\s+curiosidad|tengo\s+ganas\s+de"
    r"|me\s+gustar[ií]a\s+(?:ir|conocer|ver|saber)|esa\s+vista\s+me\s+mata"
    r"|i'?m\s+curious|i\s+want\s+to\s+know|i'?d\s+love\s+to"
    r"|that\s+(?:view|spot|place)\s+kills\s+me"
    r")",
    re.IGNORECASE,
)


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

    # 4. Drop oraciones puramente atmosféricas (clima/escenario sin gente).
    #    Esto convierte una descripción forense en interacción humana:
    #    "Mario en la playa. El clima es soleado." → "Mario en la playa."
    text = _drop_atmospheric_only(text)

    # 4b. Drop oraciones con verbos descriptivos de estado emocional
    #     pasivo ('parecen disfrutar', 'dan un toque', 'parece una buena
    #     ocasión para'). Vectrax es socio activo, no observador pasivo.
    text = _drop_passive_descriptors(text)

    # 5. Limitar a MAX_SENTENCES oraciones
    text = _limit_sentences(text, MAX_SENTENCES)

    # 6. Limpiar espacios y puntuación
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r" +([.,;:!?])", r"\1", text)
    text = text.strip()

    # 7. Capitalizar primera letra si se perdió por strip de opener
    if text and text[0].islower():
        text = text[0].upper() + text[1:]

    # 8. Reescritura a segunda persona cuando el usuario está reconocido
    #    en la foto. Convierte "a Mario y otra persona", "Mario sonríe",
    #    "una persona" (si Mario está solo) en tú/ti/segunda persona.
    user_in_faces = bool(user_name) and bool(faces) and any(
        _match_name(user_name, f) for f in (faces or [])
    )
    others = []
    if faces:
        others = [
            f for f in faces
            if not (user_name and _match_name(user_name, f))
        ]
    if user_in_faces:
        text = _rewrite_user_to_second_person(
            text, user_name=user_name, alone=(not others), lang=lang,
        )

    # 9. Continuidad contextual si hay rostros reconocidos. Si el user
    #    está SOLO en la foto, usamos un companion opener que se dirige
    #    a él directamente ("Sales genial en esa foto, Mario."). En los
    #    otros casos mantenemos la continuidad clásica ("Tú y Naomy.").
    if faces:
        if user_in_faces and not others:
            prefix = _companion_opener_for_user(
                user_name, lang=lang, seed=raw,
            )
        else:
            prefix = _continuity_prefix(faces, user_name, lang)
        if prefix and not _starts_with_names(text, faces, user_name):
            text = f"{prefix} {text}".strip()

    # 10. Tono activo de socio: si la respuesta no contiene ya una
    #     pregunta o un marcador de cierre activo (curiosidad/deseo),
    #     anexamos una pregunta corta del pool. Solo cuando hay
    #     contexto de foto real (faces o user_name) — evita inflar
    #     casos de prueba/uso sintético sin contexto de imagen.
    if faces or user_name:
        text = _ensure_curiosity_or_question(text, lang=lang, seed=raw)

    # 11. Sustancia mínima
    if len(text.strip()) < 4:
        return ""

    return text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _drop_atmospheric_only(text: str) -> str:
    """Descarta oraciones cuyo cuerpo completo es relleno atmosférico
    (clima/escenario sin gente). No toca oraciones que mezclan gente y
    ambiente. Si todo el texto era atmosférico, devuelve cadena vacía.
    """
    if not text:
        return text
    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    kept = []
    for s in sentences:
        if _ATMOSPHERIC_FULL.match(s):
            continue
        kept.append(s)
    return " ".join(kept)


def _drop_passive_descriptors(text: str) -> str:
    """Descarta oraciones que contengan verbos descriptivos de estado
    emocional pasivo o sugerencias de "buena ocasión para...".
    Vectrax es socio activo: hablamos CON el user, no DE él/ellos.
    """
    if not text:
        return text
    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    kept = []
    for s in sentences:
        is_passive = any(p.search(s) for p in _PASSIVE_DESCRIPTOR_PATTERNS)
        if is_passive:
            continue
        kept.append(s)
    return " ".join(kept)


def _ensure_curiosity_or_question(
    text: str, lang: str = "es", seed: str = "",
) -> str:
    """Garantiza que la respuesta contenga al menos UN signo de
    pregunta o un marcador de cierre activo (curiosidad/deseo). Si no,
    anexa una pregunta corta del pool. La selección es determinista
    por hash del seed — rota entre fotos distintas, estable para tests.
    """
    if not text or not text.strip():
        return text
    has_question = "?" in text or "¿" in text
    has_active_closure = bool(_ACTIVE_CLOSURE_MARKERS.search(text))
    if has_question or has_active_closure:
        return text

    pool = _CURIOSITY_QUESTIONS.get(lang) or _CURIOSITY_QUESTIONS["es"]
    if seed:
        idx = sum(ord(c) for c in seed) % len(pool)
    else:
        idx = 0
    question = pool[idx]

    # Asegurar separación natural entre el texto y la pregunta.
    text = text.rstrip()
    if text and text[-1] not in ".!?":
        text = text + "."
    return f"{text} {question}"


def _rewrite_user_to_second_person(
    text: str,
    user_name: str,
    alone: bool,
    lang: str = "es",
) -> str:
    """Reescribe referencias en tercera persona al user reconocido en
    segunda persona. No toca otros nombres.

    `alone=True` indica que el user es la única cara reconocida en la
    foto. En ese caso reescribimos también sustantivos genéricos
    singulares ("una persona") porque sabemos que se refieren a él.
    """
    if not user_name or not text:
        return text
    first = user_name.strip().split()[0]
    if not first:
        return text
    name_re = re.escape(first)

    if lang == "en":
        # "X and another person/guy/girl/man/woman" → "you and someone"
        text = re.sub(
            rf"\b{name_re}\s+and\s+(?:another|a|one)\s+(?:person|guy|girl|man|woman)\b",
            "you and someone",
            text, flags=re.IGNORECASE,
        )
        # "X is/looks/seems" → "you're/you look/you seem"
        text = re.sub(
            rf"\b{name_re}\s+is\b", "you're", text, flags=re.IGNORECASE,
        )
        text = re.sub(
            rf"\b{name_re}\s+(looks|seems|appears)\b",
            r"you \1", text, flags=re.IGNORECASE,
        )
        # bare "X" → "you"
        text = re.sub(
            rf"\b{name_re}\b", "you", text, flags=re.IGNORECASE,
        )
        if alone:
            text = _GENERIC_SUBJECT_EN.sub("you", text)
    else:
        # "a X y otra persona" → "tú y alguien"
        text = re.sub(
            rf"\ba\s+{name_re}\s+y\s+(?:otra|una)\s+persona\b",
            "tú y alguien", text, flags=re.IGNORECASE,
        )
        # "X y otra persona" → "tú y alguien"
        text = re.sub(
            rf"\b{name_re}\s+y\s+(?:otra|una)\s+persona\b",
            "tú y alguien", text, flags=re.IGNORECASE,
        )
        # "a X" (objeto preposicional) → "a ti"
        text = re.sub(
            rf"\ba\s+{name_re}\b", "a ti", text, flags=re.IGNORECASE,
        )
        # "X aparece/sonríe/posa/mira/está" → "tú apareces/sonríes/..."
        text = re.sub(
            rf"\b{name_re}\s+(aparece|sonr[ií]e|posa|mira|est[aá]|se\s+ve|se\s+r[ií]e)\b",
            lambda m: "tú " + _to_second_person_verb(m.group(1)),
            text, flags=re.IGNORECASE,
        )
        # bare "X" → "tú"
        text = re.sub(
            rf"\b{name_re}\b", "tú", text, flags=re.IGNORECASE,
        )
        if alone:
            # "una persona" / "la persona" → "tú" cuando solo él está en la foto
            text = _GENERIC_SUBJECT_ES.sub("tú", text)

    # Recapitalizar tras posibles reemplazos al inicio.
    text = text.strip()
    if text:
        # Capitaliza primera letra preservando diéresis/acentos
        text = text[0].upper() + text[1:]
    return text


_VERB_THIRD_TO_SECOND_ES = {
    "aparece": "apareces",
    "sonríe": "sonríes",
    "sonrie": "sonríes",
    "posa": "posas",
    "mira": "miras",
    "está": "estás",
    "esta": "estás",
    "se ve": "te ves",
    "se ríe": "te ríes",
    "se rie": "te ríes",
}


def _to_second_person_verb(verb: str) -> str:
    key = verb.strip().lower()
    return _VERB_THIRD_TO_SECOND_ES.get(key, verb)


def _companion_opener_for_user(
    user_name: str, lang: str = "es", seed: str = "",
) -> str:
    """Genera un opener directo al usuario cuando lo reconozco solo en
    la foto. Selección determinista por hash de `seed` (raw text) para
    que el mismo input siempre produzca el mismo opener (tests estables)
    y diferentes inputs roten naturalmente.
    """
    if not user_name:
        return ""
    pool = _COMPANION_OPENERS.get(lang) or _COMPANION_OPENERS["es"]
    first = user_name.strip().split()[0]
    if seed:
        idx = sum(ord(c) for c in seed) % len(pool)
    else:
        idx = 0
    return pool[idx].format(name=first)


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
