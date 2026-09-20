"""
Vectrax — Derivación Selectiva de Estrellas Personales con Procedencia
=========================================================================
PARTE 2 del contrato de memoria conversacional multiusuario (2026-09-20).

Causa raíz corregida: `core/operator/external_gateway.py::_bg_ingest()`
(sección "10.1 Feed the user's star") llamaba `vectrax.engine.ingest()`
de forma INCONDICIONAL para CADA mensaje entrante -- saludos ("hola"),
confirmaciones ("ok", "gracias") y preguntas incluidas -- creando una
estrella de conocimiento por mensaje sin ningún criterio de relevancia ni
traza de POR QUÉ esa estrella existe. Esto diluye el grafo y hace
imposible distinguir una afirmación personal real de ruido conversacional.

Esta corrección introduce un ÚNICO punto de derivación selectiva:
`derive_and_store_star()`. Una estrella personal solo se crea/actualiza
cuando el texto contiene información significativa -- detectada por un
vocabulario GENÉRICO de patrones (nunca nombres propios ni frases
cerradas de un caso particular) -- en una de estas categorías:

    identity | relation | preference | project | decision | commitment |
    goal | fact | state_change

Cada estrella resultante lleva procedencia completa (ver
`vectrax.models.StarProvenance` + `vectrax.db.star_provenance`):
tenant_id, user_id, categoría, entidad/tema, afirmación normalizada,
ids de los eventos de origen (ledger canónico, PARTE 1), fragmentos
literales, primera/última fecha, confianza y estado
(activo/sustituido/contradicho/incierto).

Principios (no negociables):
  - Solo texto LITERAL escrito por el propio usuario -- nunca resultados
    de búsqueda web ni inferencia del LLM sin confirmación explícita.
  - Correcciones marcan el registro anterior como "superseded", nunca se
    borra ni se reescribe.
  - Conflictos genuinos (dos afirmaciones distintas sin marcador de
    corrección) se marcan "contradicted" en ambos lados -- nunca se elige
    un ganador silenciosamente.
  - Sin nombres propios ni casos particulares hardcodeados en el
    vocabulario de detección -- funciona igual para cualquier usuario,
    cualquier tema, cualquier fecha.
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Optional

from vectrax.models import (
    STAR_CATEGORY_COMMITMENT,
    STAR_CATEGORY_DECISION,
    STAR_CATEGORY_FACT,
    STAR_CATEGORY_GOAL,
    STAR_CATEGORY_IDENTITY,
    STAR_CATEGORY_PREFERENCE,
    STAR_CATEGORY_PROJECT,
    STAR_CATEGORY_RELATION,
    STAR_CATEGORY_STATE_CHANGE,
    STAR_EXTRACTOR_VERSION,
    STAR_PROVENANCE_ACTIVE,
    STAR_PROVENANCE_CONTRADICTED,
    STAR_PROVENANCE_SUPERSEDED,
    Star,
    StarProvenance,
)

logger = logging.getLogger("vectrax.memory.star_deriver")

_DEFAULT_TENANT = "default"


# ---------------------------------------------------------------------------
# Detección de significancia — vocabulario GENÉRICO, sin nombres propios
# ---------------------------------------------------------------------------

def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


_QUESTION_RE = re.compile(
    r"^\s*(qu[ie]|qu[eé]|c[oó]mo|cu[aá]l|cu[aá]ndo|cu[aá]nto|d[oó]nde|por\s*qu[eé]|"
    r"what|who|how|when|where|why|which)\b",
    re.IGNORECASE,
)

# Frases triviales/pequeñas que NUNCA deben generar una estrella, sin
# importar cuántas veces se repitan (saludo/confirmación/relleno).
_FILLER_RE = re.compile(
    r"^\s*(hola|hey|hi|hello|buenas|buenos\s+d[ií]as|buenas\s+tardes|buenas\s+noches|"
    r"gracias|thanks|thank\s+you|ok|okay|vale|dale|listo|perfecto|genial|"
    r"s[ií]|no|adi[oó]s|bye|chao|jaja+|haha+|lol)\s*[.!?]*\s*$",
    re.IGNORECASE,
)

# Cada entrada: (categoria, patron_compilado, entidad).
# `entidad` puede ser:
#   - un string fijo -- SLOT ESTABLE para atributos de valor unico (p.ej.
#     "residence", "employer", "name") -- necesario para que una correccion
#     futura ("ahora vivo en Lisboa") encuentre y reemplace la MISMA
#     afirmacion anterior en vez de crear una entidad nueva sin relacion.
#   - None -- la entidad se deriva de un tema corto extraido tras la frase
#     disparadora (ver _short_topic) -- usado para categorias multi-valor
#     (una persona puede tener varias preferencias/proyectos/decisiones/
#     objetivos/compromisos simultaneos, cada uno es una entidad distinta).
#   - "__relation__" -- caso especial: la entidad es el sustantivo de
#     relacion capturado (p.ej. "novia", "jefe") -- tambien un slot
#     estable, pero varia segun que grupo de la alternancia ES/EN matcheo.
_CATEGORY_PATTERNS = (
    (
        STAR_CATEGORY_IDENTITY,
        re.compile(
            r"\b(?:me\s+llamo|mi\s+nombre\s+es|puedes\s+llamarme|"
            r"my\s+name\s+is|call\s+me|i'?m\s+called)\s+[a-zA-Zá-úÁ-Ú\s]{2,40}",
            re.IGNORECASE,
        ),
        "name",
    ),
    (
        STAR_CATEGORY_RELATION,
        re.compile(
            r"\bmi\s+(novia|novio|esposa|esposo|pareja|socio|socia|jefe|jefa|"
            r"hermano|hermana|amigo|amiga|mam[aá]|pap[aá]|madre|padre|hijo|hija|"
            r"prim[oa]|ti[oa]|abuel[oa]|colega)\s+(?:es|se\s+llama)\b"
            r"|\bmy\s+(girlfriend|boyfriend|wife|husband|partner|boss|brother|"
            r"sister|friend|mom|dad|mother|father|son|daughter)\s+is\b",
            re.IGNORECASE,
        ),
        "__relation__",
    ),
    # STATE_CHANGE -- variantes ESPECIFICAS que mutan un slot ya conocido
    # de FACT comparten el MISMO nombre de slot (para que la correccion
    # sea detectable) y se comprueban ANTES que el FACT generico
    # equivalente (mas abajo), porque "ya no vivo en X" es mas informativo
    # que el simple "vivo en X" que tambien contiene como subcadena.
    (
        STAR_CATEGORY_STATE_CHANGE,
        re.compile(
            r"\b(?:ya\s+no\s+vivo\s+en|ahora\s+vivo\s+en|me\s+mud[eé]\s+a|"
            r"i\s+no\s+longer\s+live\s+in|i\s+moved\s+to)\b",
            re.IGNORECASE,
        ),
        "residence",
    ),
    (
        STAR_CATEGORY_STATE_CHANGE,
        re.compile(
            r"\b(?:ya\s+no\s+trabajo\s+en|ahora\s+trabajo\s+en|i\s+no\s+longer\s+work\s+at)\b",
            re.IGNORECASE,
        ),
        "employer",
    ),
    (
        STAR_CATEGORY_COMMITMENT,
        re.compile(
            r"\b(me\s+comprometo\s+a|prometo|qued[eé]\s+en|quedamos\s+en|"
            r"i\s+promise|i\s+commit\s+to)\b",
            re.IGNORECASE,
        ),
        None,
    ),
    (
        STAR_CATEGORY_DECISION,
        re.compile(
            r"\b(decid[ií]|he\s+decidido|decidimos|"
            r"i\s+decided|we\s+decided|i'?ve\s+decided)\b",
            re.IGNORECASE,
        ),
        None,
    ),
    (
        STAR_CATEGORY_GOAL,
        re.compile(
            r"\b(mi\s+objetivo\s+es|mi\s+meta\s+es|quiero\s+lograr|aspiro\s+a|"
            r"my\s+goal\s+is|i\s+want\s+to\s+achieve|i\s+aim\s+to)\b",
            re.IGNORECASE,
        ),
        None,
    ),
    (
        STAR_CATEGORY_PROJECT,
        re.compile(
            r"\b(estoy\s+trabajando\s+en|mi\s+proyecto\s+es|estoy\s+construyendo|"
            r"estoy\s+desarrollando|"
            r"i'?m\s+working\s+on|my\s+project\s+is|i\s+am\s+building|i'?m\s+building)\b",
            re.IGNORECASE,
        ),
        None,
    ),
    (
        STAR_CATEGORY_PREFERENCE,
        re.compile(
            r"\b(me\s+encanta|me\s+gusta|me\s+fascina|no\s+me\s+gusta|odio|prefiero|"
            r"mi\s+\w+\s+favorit[oa]\s+es|"
            r"i\s+love|i\s+like|i\s+prefer|i\s+hate|my\s+favorite\s+\w+\s+is)\b",
            re.IGNORECASE,
        ),
        None,
    ),
    # FACT -- slots estables de un solo valor (comprobados DESPUES de las
    # variantes STATE_CHANGE especificas de arriba, que son mas informativas).
    (STAR_CATEGORY_FACT, re.compile(r"\b(?:vivo\s+en|i\s+live\s+in)\b", re.IGNORECASE), "residence"),
    (STAR_CATEGORY_FACT, re.compile(r"\b(?:trabajo\s+en|i\s+work\s+at)\b", re.IGNORECASE), "employer"),
    (STAR_CATEGORY_FACT, re.compile(r"\b(?:nac[ií]\s+en|i\s+was\s+born\s+in)\b", re.IGNORECASE), "birthplace"),
    (STAR_CATEGORY_FACT, re.compile(r"\b(?:estudi[eé]\s+en|i\s+studied\s+at)\b", re.IGNORECASE), "education"),
    (STAR_CATEGORY_FACT, re.compile(r"\b(?:mi\s+cumplea[ñn]os\s+es|my\s+birthday\s+is)\b", re.IGNORECASE), "birthday"),
    (STAR_CATEGORY_FACT, re.compile(r"\b(?:tengo\s+\d+\s+a[ñn]os|i'?m\s+\d+\s+years\s+old)\b", re.IGNORECASE), "age"),
    # STATE_CHANGE generico (actividades libres) -- PRIORIDAD MAS BAJA de
    # todas: solo captura lo que ninguna categoria mas especifica de
    # arriba (incluida PREFERENCE/FACT) ya reconocio.
    (
        STAR_CATEGORY_STATE_CHANGE,
        re.compile(
            r"\b(dej[eé]\s+de|empec[eé]\s+a|cambi[eé]\s+de|ya\s+no|"
            r"i\s+no\s+longer|i\s+quit|i\s+started|i\s+changed)\b",
            re.IGNORECASE,
        ),
        None,
    ),
)

_CORRECTION_MARKER_RE = re.compile(
    r"\b(ya\s+no|ahora|en\s+realidad|corrijo|correcci[oó]n|cambi[eé]|"
    r"actually|correction|i\s+meant|no\s+longer|now\s+it'?s)\b",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", _strip_accents(text.strip().lower()))


def _short_topic(text: str, match_end: int, max_words: int = 6) -> str:
    """Extrae un tema/entidad corto tras el disparador de patrón -- solo
    para indexar (no es una extracción semántica precisa)."""
    tail = text[match_end:].strip(" :,.-¡!¿?")
    words = tail.split()
    topic = " ".join(words[:max_words]) if words else text.strip()
    return _normalize(topic)[:80]


@dataclass
class SignificantDetection:
    category: str
    entity: str
    normalized_assertion: str
    confidence: float


def detect_significant(text: str) -> Optional[SignificantDetection]:
    """Devuelve la categoría/entidad detectada si el texto contiene
    información personal significativa, o None si es ruido conversacional
    (saludo, confirmación, pregunta, relleno) -- en cuyo caso NO se debe
    crear ni actualizar ninguna estrella."""
    if not text or not text.strip():
        return None
    stripped = text.strip()
    if len(stripped) < 4:
        return None
    if _FILLER_RE.match(stripped):
        return None
    if _QUESTION_RE.match(stripped) or stripped.rstrip().endswith("?"):
        # Las preguntas son consultas (ver PARTE 3/4), no declaraciones --
        # nunca generan una estrella personal por sí mismas.
        return None

    for category, pattern, entity_spec in _CATEGORY_PATTERNS:
        m = pattern.search(stripped)
        if not m:
            continue
        if entity_spec == "__relation__":
            # El primer grupo no-None entre las alternativas ES/EN -- slot
            # estable (p.ej. "novia", "jefe"), el VALOR (nombre propio) vive
            # solo en normalized_assertion/literal_fragments.
            relation_noun = next((g for g in m.groups() if g), "")
            entity = _normalize(relation_noun)
        elif entity_spec is None:
            # Categoría multi-valor -- la entidad es un tema corto extraído
            # del texto (cada afirmación distinta es una entidad distinta).
            entity = _short_topic(stripped, m.end())
        else:
            # Slot estable fijo (p.ej. "residence", "employer", "name").
            entity = entity_spec
        if not entity:
            entity = category
        confidence = 0.6
        if re.search(r"\b(mi|yo|me)\b", stripped, re.IGNORECASE) or re.search(
            r"\b(i|my|me)\b", stripped, re.IGNORECASE
        ):
            confidence = min(confidence + 0.2, 0.95)
        return SignificantDetection(
            category=category,
            entity=entity,
            normalized_assertion=_normalize(stripped),
            confidence=confidence,
        )
    return None


def _has_correction_marker(text: str) -> bool:
    return bool(_CORRECTION_MARKER_RE.search(text or ""))


# ---------------------------------------------------------------------------
# Derivación + almacenamiento
# ---------------------------------------------------------------------------

def derive_and_store_star(
    *,
    text: str,
    channel: str,
    owner: str,
    tenant_id: str = _DEFAULT_TENANT,
    source_event_id: str = "",
    explicit_user_intent: bool = False,
) -> Optional[Star]:
    """Punto único de derivación selectiva de estrellas personales.

    Solo crea/actualiza una estrella cuando `detect_significant()`
    encuentra una categoría significativa -- salvo que
    `explicit_user_intent=True` (el usuario pidió explícitamente
    "guardar"/"recuérdame", ver Strategy.RESOLVE_MEMORY), en cuyo caso se
    usa una categoría genérica de respaldo ("fact") en vez de descartar
    silenciosamente una orden directa del usuario.

    Devuelve la `Star` creada/actualizada, o None si el mensaje fue
    descartado por insignificante.
    """
    detection = detect_significant(text)
    if detection is None:
        if not explicit_user_intent:
            logger.debug(
                "star_deriver: mensaje insignificante descartado (owner=%s)",
                (owner or "")[:20],
            )
            return None
        detection = SignificantDetection(
            category=STAR_CATEGORY_FACT,
            entity=_short_topic(text.strip(), 0) or STAR_CATEGORY_FACT,
            normalized_assertion=_normalize(text),
            confidence=0.55,
        )

    from vectrax import db
    from vectrax.engine import ingest

    now = time.time()
    existing = db.find_active_star_provenance(
        tenant_id=tenant_id, channel=channel, owner=owner,
        entity=detection.entity,
    )

    if existing is not None and existing["normalized_assertion"] == detection.normalized_assertion:
        # Misma afirmación repetida -- refuerza el registro existente,
        # nunca crea un duplicado de procedencia.
        star = ingest(text=text, channel=channel, owner=owner)
        db.touch_star_provenance(
            existing["id"], last_seen=now,
            add_source_event_id=source_event_id, add_literal_fragment=text,
            confidence=min(existing.get("confidence", 0.0) + 0.05, 0.99),
        )
        return star

    previous_star_id = ""
    new_status = STAR_PROVENANCE_ACTIVE
    if existing is not None:
        previous_star_id = existing["star_id"]
        if _has_correction_marker(text):
            # Corrección explícita del propio usuario -- el registro
            # anterior queda marcado como sustituido, NUNCA se borra.
            db.mark_star_provenance_status(existing["id"], STAR_PROVENANCE_SUPERSEDED)
            new_status = STAR_PROVENANCE_ACTIVE
        else:
            # Dos afirmaciones distintas para la misma entidad+categoría
            # SIN marcador de corrección -- conflicto genuino. Nunca se
            # elige un ganador silenciosamente: ambas quedan marcadas
            # 'contradicted' para que la recuperación (PARTE 3) declare
            # el conflicto en vez de fabricar certeza.
            db.mark_star_provenance_status(existing["id"], STAR_PROVENANCE_CONTRADICTED)
            new_status = STAR_PROVENANCE_CONTRADICTED

    star = ingest(text=text, channel=channel, owner=owner)
    prov = StarProvenance(
        star_id=star.id,
        tenant_id=tenant_id,
        channel=channel,
        owner=owner,
        category=detection.category,
        entity=detection.entity,
        normalized_assertion=detection.normalized_assertion,
        source_event_ids=[source_event_id] if source_event_id else [],
        literal_fragments=[text],
        confidence=detection.confidence,
        status=new_status,
        extractor_version=STAR_EXTRACTOR_VERSION,
        previous_star_id=previous_star_id,
        first_seen=now,
        last_seen=now,
    )
    db.insert_star_provenance(prov)
    logger.info(
        "star_deriver: estrella derivada | owner=%s category=%s entity=%s status=%s",
        (owner or "")[:20], detection.category, detection.entity[:40], new_status,
    )
    return star
