"""
Vectrax Semantic Intent Classifier — Clasificador Semántico de Intención
=========================================================================
Reemplaza la detección por keywords con comprensión semántica de intención.

Fases:
  1. Frame Detection  — detecta el marco comunicativo (búsqueda, identidad,
                        afirmación, comando) analizando estructura sintáctica
  2. Entity Extraction — extrae entidades con contexto (ubicación, categoría,
                         persona, tiempo, preferencia, acción)
  3. Intent Scoring   — puntúa cada intent candidato por combinación de
                        frame + entidades + señales contextuales

Privacidad:
  SemanticResult.entities NO contiene texto crudo del mensaje.
  Solo categorías abstractas y valores extraídos.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("vectrax.semantic_classifier")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SemanticIntent(str, Enum):
    """Intención semántica del mensaje."""
    PLACE_SEARCH   = "place_search"     # buscar lugar físico / negocio
    WEB_SEARCH     = "web_search"       # pregunta factual / noticias / info
    MEMORY_LOOKUP  = "memory_lookup"    # consulta sobre memoria propia
    IDENTITY_QUERY = "identity_query"   # quién soy / mi nombre / datos personales
    GENERAL_CHAT   = "general_chat"     # conversación general / saludo
    SYSTEM_ACTION  = "system_action"    # comando del sistema o acción ejecutable
    MARKET_QUERY   = "market_query"     # precio, tendencia, análisis de mercado
    MIXED          = "mixed"            # múltiples intenciones detectadas


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------

@dataclass
class Entity:
    """Entidad extraída del texto con contexto."""
    type: str       # location, category, person, object, time, preference, action
    value: str      # valor extraído
    confidence: float = 0.8


@dataclass
class SemanticResult:
    """Resultado completo del clasificador semántico."""
    intent: SemanticIntent
    secondary_intents: List[SemanticIntent] = field(default_factory=list)
    confidence: float = 0.0
    entities: List[Entity] = field(default_factory=list)
    suggested_tools: List[str] = field(default_factory=list)
    frame: str = ""                     # marco comunicativo detectado
    scores: Dict[str, float] = field(default_factory=dict)
    domain: str = ""                    # dominio de conocimiento (market, freight_logistics, …)
    domain_confidence: float = 0.0     # confianza de la detección de dominio
    classified_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent.value,
            "secondary_intents": [i.value for i in self.secondary_intents],
            "confidence": round(self.confidence, 4),
            "entities": [
                {"type": e.type, "value": e.value, "confidence": round(e.confidence, 2)}
                for e in self.entities
            ],
            "suggested_tools": self.suggested_tools,
            "frame": self.frame,
            "domain": self.domain,
            "domain_confidence": round(self.domain_confidence, 4),
        }

    def get_entities_by_type(self, entity_type: str) -> List[Entity]:
        return [e for e in self.entities if e.type == entity_type]

    def has_entity(self, entity_type: str) -> bool:
        return any(e.type == entity_type for e in self.entities)


# ---------------------------------------------------------------------------
# Mapeo de herramientas sugeridas por intent
# ---------------------------------------------------------------------------

_INTENT_TOOLS: Dict[SemanticIntent, List[str]] = {
    SemanticIntent.PLACE_SEARCH:   ["google_places", "location_api"],
    SemanticIntent.WEB_SEARCH:     ["web_search", "ddg", "brave"],
    SemanticIntent.MEMORY_LOOKUP:  ["memory", "resolve_local"],
    SemanticIntent.IDENTITY_QUERY: ["user_memory", "identity_anchor"],
    SemanticIntent.GENERAL_CHAT:   ["llm"],
    SemanticIntent.SYSTEM_ACTION:  ["command_executor"],
    SemanticIntent.MARKET_QUERY:   ["market_vigilance", "binance"],
    SemanticIntent.MIXED:          ["llm", "web_search"],
}


# ---------------------------------------------------------------------------
# Phase 1: Frame Detection — marcos comunicativos
# ---------------------------------------------------------------------------
# Cada frame es un patrón sintáctico que identifica la ESTRUCTURA del
# mensaje, no keywords sueltos. El frame determina qué tipo de acción
# espera el usuario.

class _Frame(str, Enum):
    SEARCH_PLACE    = "search_place"     # buscar lugar / negocio
    SEARCH_INFO     = "search_info"      # buscar información factual
    SEARCH_NEWS     = "search_news"      # qué pasó / noticias
    MARKET_DATA     = "market_data"      # precio / tendencia / análisis crypto/stock
    ASK_IDENTITY    = "ask_identity"     # quién soy / mi nombre
    ASK_MEMORY      = "ask_memory"       # qué recuerdas / qué sabes de mí
    STORE_DATA      = "store_data"       # recuérdame / guarda esto
    COMMAND         = "command"          # /comando
    GREETING        = "greeting"         # hola / cómo estás
    STATEMENT       = "statement"        # afirmación general
    REQUEST_ACTION  = "request_action"   # crea / modifica / analiza


# Patrones de frame — el orden importa (más específicos primero)
# Cada tupla: (pattern, frame, base_weight)
_FRAME_PATTERNS: List[Tuple[re.Pattern, _Frame, float]] = [
    # === COMANDOS explícitos ===
    (re.compile(r"^/\w+", re.I), _Frame.COMMAND, 1.0),

    # === MERCADO (crypto, stocks, análisis) ===
    # IDEA-57101B43: semántico clasificaba market queries como WEB_SEARCH.
    # Estos patrones detectan market intent ANTES de SEARCH_INFO.
    # Peso 0.95: 0.95 * 1.0 = 0.95 → conf 0.475, reforzado por entity boost.

    # "precio de btc", "bitcoin price", "cotización del eth"
    (re.compile(
        r"\b(?:precio|price|cotizaci[oó]n|valor|cotizacion)\b"
        r".*\b(?:btc|bitcoin|eth|ethereum|bnb|sol|ada|dot|xrp|avax|matic|link|"
        r"spy|qqq|aapl|tsla|nvda|crypto|cripto)\b",
        re.I,
    ), _Frame.MARKET_DATA, 0.95),

    # Reversed: "btc precio", "bitcoin hoy", "eth now"
    (re.compile(
        r"\b(?:btc|bitcoin|eth|ethereum|bnb|sol|ada|dot|xrp|avax|matic|link)\b"
        r".*\b(?:precio|price|cotizaci[oó]n|valor|hoy|today|now|ahora|status|"
        r"tendencia|trend|an[aá]lisis|analysis|momentum|breakout)\b",
        re.I,
    ), _Frame.MARKET_DATA, 0.95),

    # "cuánto vale/cuesta btc", "cómo está/va bitcoin"
    (re.compile(
        r"\b(?:cu[aá]nto|how\s+much)\b.*\b(?:vale|cuesta|est[aá]|is|costs?)\b"
        r".*\b(?:btc|bitcoin|eth|ethereum|bnb|sol)\b",
        re.I,
    ), _Frame.MARKET_DATA, 0.95),
    (re.compile(
        r"\b(?:c[oó]mo|how)\b.*\b(?:est[aá]|va|is|going)\b"
        r".*\b(?:btc|bitcoin|eth|ethereum|el\s+mercado|the\s+market)\b",
        re.I,
    ), _Frame.MARKET_DATA, 0.90),

    # "mercado crypto", "resumen del mercado", "market snapshot"
    (re.compile(
        r"\b(?:mercado|market)\b.*\b(?:crypto|cripto|stock|bolsa|resumen|snapshot|estado)\b",
        re.I,
    ), _Frame.MARKET_DATA, 0.90),

    # Standalone tickers as full message: "btc", "bitcoin", "eth?"
    (re.compile(
        r"^\s*(?:btc|bitcoin|ethereum|eth|bnb|sol|ada|dot|avax|matic|link|xrp)\s*[?]?\s*$",
        re.I,
    ), _Frame.MARKET_DATA, 0.95),

    # === BÚSQUEDA DE LUGAR ===
    # Estructura: verbo_búsqueda + artículo? + tipo_servicio + ubicación?
    (re.compile(
        r"\b(?:búscame|buscame|busca|busco|busque|encuentra|encontrar|buscar)\b"
        r".*\b(?:un|una|el|la|los|las|algún|alguna)\b"
        r".*\b(?:en|cerca|por|de)\b",
        re.I,
    ), _Frame.SEARCH_PLACE, 0.85),

    # Estructura: dónde + verbo_acción + verbo/sustantivo_servicio
    (re.compile(
        r"\b(?:d[oó]nde|donde)\b\s+\b(?:hay|está|esta|queda|encuentro|puedo|consigo|"
        r"compro|venden|reparo|arreglo|lavo|como|ceno|desayuno|almuerzo)\b",
        re.I,
    ), _Frame.SEARCH_PLACE, 0.9),

    # Estructura: necesito/quiero/busco + artículo + sustantivo + cerca/en
    (re.compile(
        r"\b(?:necesito|quiero|busco|ocupo)\b\s+(?:un|una|el|la)\b"
        r".*\b(?:cerca|cercano|cercana|abierto|abierta|en\s+\w+)\b",
        re.I,
    ), _Frame.SEARCH_PLACE, 0.85),

    # Estructura: busco/necesito/quiero + artículo + tipo_lugar (sin exigir ubicación)
    (re.compile(
        r"\b(?:busco|necesito|quiero|ocupo)\b\s+(?:un|una|el|la)\s+"
        r"(?:restaurante|farmacia|taller|mec[aá]nico|mecanico|tienda|banco|"
        r"hospital|cl[ií]nica|clinica|dentista|veterinari[oa]|hotel|gimnasio|"
        r"barber[ií]a|barberia|peluquer[ií]a|peluqueria|cafeter[ií]a|cafeteria|"
        r"panader[ií]a|panaderia|lavander[ií]a|lavanderia|cerrajero|electricista|"
        r"plomero|fontanero|abogad[oa]|notar[ií]a|notaria|ferreter[ií]a|ferreteria|"
        r"librer[ií]a|libreria|bar|pub|spa|supermercado|gasolinera)",
        re.I,
    ), _Frame.SEARCH_PLACE, 0.85),

    # Estructura: dame/dime la dirección/ubicación/teléfono de
    (re.compile(
        r"\b(?:dame|dime|deme|cu[aá]l\s+es)\b\s+(?:la\s+)?"
        r"(?:direcci[oó]n|direccion|ubicaci[oó]n|ubicacion|tel[eé]fono|telefono)\b"
        r".*\b(?:de|del)\b",
        re.I,
    ), _Frame.SEARCH_PLACE, 0.85),

    # Estructura: sustantivo_lugar (standalone or with context)
    # Covers both "restaurante en Miami" AND "un restaurante bueno"
    # Weight 0.82 → 0.82 * 1.25 = 1.025 → conf 0.5125 (> threshold 0.5)
    (re.compile(
        r"\b(?:restaurantes?|farmacias?|hoteles?|talleres?|mec[aá]nicos?|mecanicos?|"
        r"tiendas?|bancos?|hospitales?|cl[ií]nicas?|clinicas?|gimnasios?|bares?|"
        r"caf[eé]s?|cafes?|abogados?|dentistas?|veterinarios?|peluquer[ií]as?|peluquerias?|"
        r"supermercados?|gasolineras?|ferreter[ií]as?|lavanderias?|cerrajeros?|"
        r"electricistas?|plomeros?|fontaneros?)\b"
        r".*\b(?:en|in|de|cerca|bueno|buen[ao]|mejor|mejor[ea]s|abierto|abierta|barato|econ[oó]mico)\b",
        re.I,
    ), _Frame.SEARCH_PLACE, 0.82),

    # Sustantivo_lugar + en + CIUDAD (original pattern, higher confidence)
    (re.compile(
        r"\b(?:restaurantes?|farmacias?|hoteles?|talleres?|mec[aá]nicos?|mecanicos?|"
        r"tiendas?|bancos?|hospitales?|cl[ií]nicas?|clinicas?|gimnasios?|bares?|"
        r"caf[eé]s?|cafes?|abogados?|dentistas?|veterinarios?|peluquer[ií]as?|peluquerias?)\b"
        r".*\b(?:en|in)\b\s+[A-ZÁÉÍÓÚÑ]",
        re.I,
    ), _Frame.SEARCH_PLACE, 0.88),

    # Estructura: hay + artículo + sustantivo_lugar + cerca
    (re.compile(
        r"\b(?:hay)\b\s+(?:un|una|algún|alguna)\b\s+\w+\s+"
        r"(?:cerca|abierto|abierta|por aquí|por aqui)",
        re.I,
    ), _Frame.SEARCH_PLACE, 0.85),

    # English: find/search + place near/in
    (re.compile(
        r"\b(?:find|search|looking\s+for|where\s+(?:is|can\s+i\s+find))\b"
        r".*\b(?:near|nearby|close|in\s+\w+)\b",
        re.I,
    ), _Frame.SEARCH_PLACE, 0.85),

    # English: best/top/good + place_type + in CITY
    (re.compile(
        r"\b(?:best|top|good|great|nice|cheap|closest)\b"
        r".*\b(?:restaurant|cafe|coffee|shop|store|hotel|bar|gym|bakery|"
        r"pharmacy|hospital|dentist|mechanic|lawyer|barber|salon|laundry)s?\b"
        r".*\b(?:in|near|around|close\s+to)\b\s+[A-Z]",
        re.I,
    ), _Frame.SEARCH_PLACE, 0.85),

    # === IDENTIDAD ===
    (re.compile(
        r"\b(?:qui[eé]n\s+soy|c[oó]mo\s+me\s+llamo|cu[aá]l\s+es\s+mi\s+nombre|"
        r"who\s+am\s+i|what'?s?\s+my\s+name|sabes\s+(?:qui[eé]n|c[oó]mo)\s+(?:soy|me\s+llamo)|"
        r"me\s+conoces|sabes\s+mi\s+nombre|recuerdas\s+mi\s+nombre)\b",
        re.I,
    ), _Frame.ASK_IDENTITY, 0.95),

    # === MEMORIA (preguntas sobre lo que Vectrax recuerda) ===
    # Cubre: "qué sabes de mí", "qué recuerdas", "háblame de mi memoria",
    #        "qué dije", "qué hablamos", "lo que te conté", "mi historial", etc.
    # FIX IDEA-7A0B846D: añadidos patrones de "háblame/cuéntame de mi memoria"
    # y "tell me about my memory" que antes caían a SEARCH_INFO.
    # Peso subido de 0.9 a 0.93 para mayor margen sobre SEARCH_INFO (0.75).
    (re.compile(
        r"\b(?:qu[eé]\s+sabes\s+(?:de|sobre)\s+m[ií]|"
        r"qu[eé]\s+recuerdas|recuerdas\s+(?:lo\s+que|cuando|que)|"
        r"qu[eé]\s+(?:te\s+)?dije|qu[eé]\s+hablamos|"
        r"qu[eé]\s+(?:te\s+)?cont[eé]|qu[eé]\s+escrib[ií]|"
        r"lo\s+que\s+(?:te\s+)?(?:dije|cont[eé]|escrib[ií])|"
        r"mi\s+(?:historial|memoria|notas|conversaci[oó]n|datos|perfil|informaci[oó]n)|"
        r"mis\s+(?:preferencias|intereses|mensajes|recuerdos|datos)|"
        r"(?:h[aá]blame|cu[eé]ntame|dime|hablame|cuentame)\s+(?:de|sobre)\s+mi\s+(?:memoria|historial|datos|perfil|informaci[oó]n)|"
        r"(?:h[aá]blame|cu[eé]ntame|hablame|cuentame)\s+(?:de|sobre)\s+m[ií]|"
        r"(?:h[aá]blame|cu[eé]ntame|hablame|cuentame)\s+(?:de|sobre)\s+lo\s+que\s+(?:sabes|recuerdas|tienes)|"
        r"what\s+(?:did\s+i\s+(?:say|write|tell)|i\s+said|i\s+wrote)|"
        r"what\s+do\s+you\s+(?:know|remember)\s+about\s+me|"
        r"tell\s+me\s+about\s+my\s+(?:memory|history|data|profile)|"
        r"my\s+(?:history|memory|notes|data|messages|profile))\b",
        re.I,
    ), _Frame.ASK_MEMORY, 0.93),

    # === MEMORIA — preguntas personales contextuales ===
    # Cubre los 78 casos de conflicto sem=memory→regex=online donde el
    # clasificador detecta MEMORY con baja confianza (0.30-0.50) porque
    # la pregunta usa pronombres personales pero no el patrón exacto de
    # ASK_MEMORY. Peso 0.88 garantiza que supere SEARCH_INFO (0.70).
    #
    # Patrones:
    #   «¿tienes/hay/guardaste algo sobre mí/de mí?»
    #   «¿sabes/recuerdas algo sobre/de mí?»
    #   «¿lo tienes/guardaste/apuntaste/registraste?»
    #   «¿cuándo fue/pasó/ocurrió eso?»  (contexto personal — pronombres)
    #   «¿y mi X?» / «¿mi X está...?» (referencia a dato personal)
    #   «do you have/remember anything about me?"
    (re.compile(
        r"(?:"
        r"\b(?:tienes?|hay|guardaste?)\b.{0,30}\b(?:algo|info|datos?|registro|nota)\b.{0,15}\b(?:(?:de|sobre)\s+m[ií]|m[ií]o?)\b"
        r"|\b(?:sabes?|recuerdas?|conoces?)\b.{0,25}\b(?:algo\b.{0,15})?\b(?:(?:de|sobre)\s+m[ií]|m[ií]o?)\b"
        r"|\b(?:lo|la)\s+(?:tienes?|guardaste?|apuntaste?|registraste?|recordaste?)\b"
        r"|\b(?:a[uú]n\s+)?(?:recuerdas?|tienes?)\s+(?:eso|esto|aquello|lo\s+que|lo\s+del?|lo\s+de\s+)\b"
        r"|\b(?:y\s+)?mi[sS]?\s+(?:\w+\s+){0,3}(?:está|estaba|sigue|quedó|cambió)\b"
        r"|\b(?:cu[aá]ndo|c[oó]mo|d[oó]nde)\s+(?:fue|pas[oó]|ocurri[oó]|qued[oó])\s+(?:eso|esto|lo)\b"
        r"|\bdo\s+you\s+(?:have|remember|know)\b.{0,20}\b(?:about\s+me|from\s+me|i\s+(?:said|told|wrote))\b"
        r")",
        re.I,
    ), _Frame.ASK_MEMORY, 0.88),

    # Estructura: recuérdame / guarda / anota
    (re.compile(
        r"\b(?:recu[eé]rdame|anota|guarda|apunta|registra|remember\s+(?:this|that)|"
        r"save|store|note\s+(?:this|that))\b",
        re.I,
    ), _Frame.STORE_DATA, 0.85),

    # === BÚSQUEDA DE NOTICIAS / EVENTOS ===
    (re.compile(
        r"\b(?:qu[eé]\s+pas[oó]|qu[eé]\s+(?:ha\s+)?(?:pasado|ocurrido|sucedido)|"
        r"noticias\s+(?:de|sobre|en)|últimas?\s+noticias|"
        r"what\s+happened|latest\s+news|news\s+(?:about|in|from))\b",
        re.I,
    ), _Frame.SEARCH_NEWS, 0.85),

    # === BÚSQUEDA DE INFORMACIÓN GENERAL ===
    # Estructura: verbo_pregunta + qué/quién/cómo/cuándo/cuánto/por qué
    # Peso 0.75: 0.75 * 1.40 (WEB_SEARCH map) = 1.05 → conf 0.525 (> threshold 0.5).
    # EXCEPTION: preguntas con pronombres personales (mí, mi, yo, me, I, my)
    # son MEMORY, no SEARCH_INFO — handled by ASK_MEMORY patterns above.
    (re.compile(
        r"^(?:qu[eé]|qui[eé]n|c[oó]mo|cu[aá]ndo|cu[aá]ntos?|d[oó]nde|por\s+qu[eé]|cu[aá]l)\b"
        r"(?!.*\b(?:m[ií]s?|yo|me\b|m[ií]o|conmigo|my|I\s))"
        r".*(?:\?|$)",
        re.I,
    ), _Frame.SEARCH_INFO, 0.75),

    # Estructura: explica/dime/define + tema
    (re.compile(
        r"\b(?:explica|expl[ií]came|explicame|dime|define|describe|analiza|compara|resume)\b"
        r"\s+.{3,}",
        re.I,
    ), _Frame.SEARCH_INFO, 0.75),

    # English question patterns
    # Peso 0.73: 0.73 * 1.40 = 1.022 → conf 0.511 (> threshold 0.5).
    # EXCEPTION: personal questions (about me, my, I) are MEMORY.
    (re.compile(
        r"^(?:what|who|where|when|why|how|which|is|are|was|were|do|does|did|can|could|should|would|will)\b"
        r"(?!.*\b(?:my\b|about\s+me|I\s+(?:said|told|wrote|did|have)))"
        r".*\??\s*$",
        re.I,
    ), _Frame.SEARCH_INFO, 0.73),

    # === ACCIÓN SOBRE EL SISTEMA ===
    (re.compile(
        r"\b(?:crea|crear|modifica|modificar|genera|generar|implementa|implementar|"
        r"construye|construir|despliega|desplegar|configura|configurar|"
        r"create|build|implement|deploy|configure|setup)\b",
        re.I,
    ), _Frame.REQUEST_ACTION, 0.7),

    # === SALUDO ===
    (re.compile(
        r"^(?:hola|hey|hi|hello|buenos?\s+(?:d[ií]as|tardes?|noches?)|"
        r"good\s+(?:morning|afternoon|evening)|saludos|yo)\s*[!.?]?\s*$",
        re.I,
    ), _Frame.GREETING, 0.9),

    # === AFIRMACIÓN / NOTA PERSONAL (statement) ===
    # Detecta enunciados en 1ra persona que son notas, registros o
    # afirmaciones personales — NO preguntas, NO comandos.
    # Peso bajo (0.65) para no interferir con frames más específicos.
    (re.compile(
        r"(?:"
        # 1ra persona pasada: "hoy hice", "ayer compré", "aprendí"
        r"\b(?:hoy|ayer|anoche|esta\s+(?:mañana|tarde|noche|semana)|mañana)\b"
        r".+\b(?:hice|tuve|fui|compr[eé]|aprend[ií]|termin[eé]|empec[eé]|trabaj[eé]|vi|com[ií]|corr[ií]|le[ií]|escrib[ií]|habl[eé])\b"
        # nota/recordatorio: "nota:", "reunión a las", "mi plan"
        r"|^(?:nota|recordatorio|pendiente|tarea)\s*[:;.\-]"
        r"|\b(?:reuni[oó]n|cita|evento|compromiso)\s+(?:a\s+las|con|para|de|en)\b"
        r"|\bmi\s+(?:plan|objetivo|meta|idea|decisi[oó]n|resoluci[oó]n)\b"
        # self-report: "me siento", "estoy bien", "tengo que"
        r"|\b(?:me\s+(?:siento|sient|fue|qued[eé]|falta)|estoy\s+(?:bien|mal|cansad|content|ocupad|enferm))\b"
        # English 1st person statements
        r"|\b(?:today|yesterday|this\s+(?:morning|week|evening))\b"
        r".+\b(?:I\s+(?:did|had|went|bought|learned|finished|started|worked|saw|ate|ran|wrote|spoke))\b"
        r"|^(?:note|reminder|todo)\s*[:;.\-]"
        r"|\b(?:meeting|appointment)\s+(?:at|with|for)\b"
        r"|\bmy\s+(?:plan|goal|idea|decision)\b"
        r"|\b(?:I\s+feel|I'?m\s+(?:fine|good|tired|busy|sick))\b"
        r")",
        re.I,
    ), _Frame.STATEMENT, 0.85),

    # === MENCIÓN CASUAL / PREFERENCIA (statement, NO búsqueda) ===
    # "trabajo en un banco", "vivo en un hotel", "fui al gimnasio",
    # "me gusta la comida italiana" — enunciado personal, no place_search.
    # Da una base GENERAL_CHAT a estos casos para que una palabra de lugar
    # mencionada casualmente NO dispare place_search (ni el embedding boost,
    # que se desactiva cuando ya hay una señal de frame fuerte).
    (re.compile(
        r"\b(?:trabajo\s+en|trabajaba\s+en|vivo\s+en|viv[ií]a\s+en|"
        r"estudi[eé]\s+en|estudio\s+en|fui\s+al?|fuimos\s+al?|voy\s+al?|"
        r"conozco\s+(?:un|una)|me\s+gusta(?:n)?\s+(?:el|la|los|las)|"
        r"i\s+work\s+(?:at|in)|i\s+live\s+(?:in|near)|i\s+went\s+to)\b",
        re.I,
    ), _Frame.STATEMENT, 0.85),
]


def _detect_frames(text: str) -> List[Tuple[_Frame, float]]:
    """Detecta todos los marcos comunicativos presentes en el texto."""
    detected: List[Tuple[_Frame, float]] = []
    for pattern, frame, weight in _FRAME_PATTERNS:
        if pattern.search(text):
            detected.append((frame, weight))
    return detected


# ---------------------------------------------------------------------------
# Phase 2: Entity Extraction — extracción contextual de entidades
# ---------------------------------------------------------------------------

# Categorías de lugar/servicio — se evalúan CON contexto de búsqueda
_PLACE_CATEGORIES: Dict[str, str] = {
    # ES
    "restaurante": "restaurant", "restaurant": "restaurant",
    "italiano": "italian_restaurant", "mexicano": "mexican_restaurant",
    "sushi": "sushi_restaurant", "chino": "chinese_restaurant",
    "comida": "restaurant", "cena": "restaurant", "cenar": "restaurant",
    "almuerzo": "restaurant", "desayuno": "restaurant",
    "farmacia": "pharmacy", "pharmacy": "pharmacy",
    "supermercado": "supermarket", "tienda": "store",
    "gasolinera": "gas_station", "banco": "bank",
    "hospital": "hospital", "clínica": "clinic", "clinica": "clinic",
    "dentista": "dentist", "doctor": "doctor", "médico": "doctor",
    "veterinario": "veterinary", "veterinaria": "veterinary",
    "hotel": "hotel", "hospedaje": "lodging",
    "gimnasio": "gym", "gym": "gym",
    "barbería": "barber", "barberia": "barber",
    "peluquería": "hair_salon", "peluqueria": "hair_salon",
    "cafetería": "cafe", "cafeteria": "cafe", "café": "cafe", "cafe": "cafe",
    "bar": "bar", "pub": "pub",
    "panadería": "bakery", "panaderia": "bakery",
    "lavandería": "laundry", "lavanderia": "laundry",
    "taller": "car_repair", "mecánico": "car_repair", "mecanico": "car_repair",
    "abogado": "lawyer", "abogada": "lawyer",
    "notaría": "notary", "notaria": "notary",
    "cerrajero": "locksmith", "electricista": "electrician",
    "plomero": "plumber", "fontanero": "plumber",
    "escuela": "school", "colegio": "school", "universidad": "university",
    "cine": "movie_theater", "museo": "museum", "teatro": "theater",
    "aeropuerto": "airport", "estación": "transit_station",
    "spa": "spa", "piscina": "pool", "cancha": "sports_field",
    "ferretería": "hardware_store", "ferreteria": "hardware_store",
    "librería": "bookstore", "libreria": "bookstore",
    "pizzería": "pizza", "pizzeria": "pizza",
    "heladería": "ice_cream", "heladeria": "ice_cream",
}

# Patrones de tiempo
_TIME_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:ahora|now|ahorita|ya)\b", re.I), "now"),
    (re.compile(r"\b(?:hoy|today)\b", re.I), "today"),
    (re.compile(r"\b(?:ayer|yesterday)\b", re.I), "yesterday"),
    (re.compile(r"\b(?:mañana|tomorrow)\b", re.I), "tomorrow"),
    (re.compile(r"\b(?:esta\s+(?:semana|noche|tarde|mañana)|tonight|this\s+week)\b", re.I), "this_period"),
    (re.compile(r"\b(?:abierto|abierta|open)\b", re.I), "operating_now"),
]

# Patrones de ubicación — ciudades y señales de lugar
_LOCATION_PATTERN = re.compile(
    r"\b(?:en|in|at|de|from|cerca\s+de|near)\b\s+"
    r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)*)",
)

_PROXIMITY_PATTERN = re.compile(
    r"\b(?:cerca|cercano|cercana|nearby|near\s*(?:me|by|here)|"
    r"por\s+aqu[ií]|aqu[ií]\s+cerca|close\s+(?:to|by))\b",
    re.I,
)

# Verbos de acción solicitada
_ACTION_VERBS: Dict[str, str] = {
    "busca": "search", "búscame": "search", "buscame": "search",
    "buscar": "search", "encuentra": "search", "encontrar": "search",
    "recomienda": "recommend", "recomiéndame": "recommend",
    "muestra": "show", "muéstrame": "show", "muestrame": "show",
    "dime": "tell", "explica": "explain", "define": "define",
    "compara": "compare", "analiza": "analyze", "resume": "summarize",
    "recuérdame": "remember", "recuerdame": "remember",
    "guarda": "store", "anota": "note",
    "crea": "create", "genera": "generate", "modifica": "modify",
}

# Verbos que indican contexto de BÚSQUEDA de lugar (no mención casual)
_SEARCH_CONTEXT_VERBS = re.compile(
    r"\b(?:busca|busco|búscame|buscame|buscar|encuentra|encontrar|necesito|"
    r"quiero|ocupo|hay|dónde|donde|cerca|recomienda|recomié?ndame|"
    r"dame|dime|deme|direcci[oó]n|direccion|ubicaci[oó]n|ubicacion|"
    r"find|search|looking\s+for|where|need|want)\b",
    re.I,
)

# Verbos que indican mención CASUAL (no búsqueda)
_CASUAL_CONTEXT = re.compile(
    r"\b(?:trabajo\s+en|vivo\s+en|estudi[eé]\s+en|fui\s+a|"
    r"conozco\s+(?:un|una)|me\s+gusta\s+(?:el|la)|"
    r"i\s+work\s+(?:at|in)|i\s+live\s+(?:in|near))\b",
    re.I,
)


def _extract_entities(text: str, frames: List[Tuple[_Frame, float]]) -> List[Entity]:
    """Extrae entidades del texto considerando el contexto de los frames."""
    entities: List[Entity] = []
    lower = text.lower()
    frame_set = {f for f, _ in frames}
    has_search_frame = _Frame.SEARCH_PLACE in frame_set
    has_search_verbs = bool(_SEARCH_CONTEXT_VERBS.search(text))
    has_casual_context = bool(_CASUAL_CONTEXT.search(text))

    # — Categoría de lugar/servicio —
    for keyword, category in _PLACE_CATEGORIES.items():
        if re.search(rf"\b{re.escape(keyword)}\b", lower):
            # Solo marcar como entidad de lugar si hay contexto de búsqueda
            # "trabajo en un banco" → casual, no búsqueda
            # "busco un banco" → búsqueda
            if has_search_frame or (has_search_verbs and not has_casual_context):
                conf = 0.9 if has_search_frame else 0.65
                entities.append(Entity(type="category", value=category, confidence=conf))
                break  # solo una categoría principal

    # — Ubicación —
    loc_match = _LOCATION_PATTERN.search(text)
    if loc_match:
        location = loc_match.group(1).strip()
        if len(location) >= 2 and location not in ("Un", "Una", "El", "La", "Los", "Las"):
            entities.append(Entity(type="location", value=location, confidence=0.85))

    # — Proximidad —
    if _PROXIMITY_PATTERN.search(text):
        entities.append(Entity(type="location", value="nearby", confidence=0.8))

    # — Tiempo —
    for pattern, time_val in _TIME_PATTERNS:
        if pattern.search(text):
            entities.append(Entity(type="time", value=time_val, confidence=0.85))
            break

    # — Acción solicitada —
    for verb, action in _ACTION_VERBS.items():
        if re.search(rf"\b{re.escape(verb)}\b", lower):
            entities.append(Entity(type="action", value=action, confidence=0.8))
            break

    # — Persona (si hay frame de identidad/memoria) —
    if _Frame.ASK_IDENTITY in frame_set or _Frame.ASK_MEMORY in frame_set:
        entities.append(Entity(type="person", value="self", confidence=0.9))

    # — Preferencia (si el texto indica preferencia) —
    pref_match = re.search(
        r"\b(?:prefiero|me\s+gusta|favorito|preferido|i\s+prefer|i\s+like|favorite)\b"
        r"\s+(.{2,40}?)(?:[,.\n]|$)",
        text, re.I,
    )
    if pref_match:
        entities.append(Entity(
            type="preference",
            value=pref_match.group(1).strip()[:40],
            confidence=0.75,
        ))

    return entities


# ---------------------------------------------------------------------------
# Phase 3: Intent Scoring — puntuación contextual
# ---------------------------------------------------------------------------

# Mapeo frame → intents candidatos con peso base
# Pesos calibrados por aprendizaje adaptativo.
# Ajustados según correlación frame→success en datos reales:
#   - SEARCH_INFO: 100% success con conf=0.28 → subir peso para cruzar threshold
#   - ASK_MEMORY: 100% success con conf=0.50 → reforzar para margen de seguridad
#   - STATEMENT: 100% success con conf=0.51 → estable (ya ajustado)
_FRAME_INTENT_MAP: Dict[_Frame, List[Tuple[SemanticIntent, float]]] = {
    _Frame.SEARCH_PLACE:   [(SemanticIntent.PLACE_SEARCH, 1.25)],
    _Frame.SEARCH_INFO:    [(SemanticIntent.WEB_SEARCH, 1.4), (SemanticIntent.GENERAL_CHAT, 0.2)],
    _Frame.SEARCH_NEWS:    [(SemanticIntent.WEB_SEARCH, 1.2)],
    # MARKET_DATA: peso 1.3 → 0.95*1.3 = 1.235 → conf 0.617 (> threshold 0.5).
    # Supera WEB_SEARCH (0.75*1.4=1.05 → conf 0.525) cuando ambos matchean.
    _Frame.MARKET_DATA:    [(SemanticIntent.MARKET_QUERY, 1.3)],
    _Frame.ASK_IDENTITY:   [(SemanticIntent.IDENTITY_QUERY, 1.0)],
    # ASK_MEMORY weight subido de 1.35 → 1.45 (FIX IDEA-7A0B846D: 18 conflictos).
    # Garantiza que MEMORY_LOOKUP gane sobre WEB_SEARCH cuando ambos frames
    # se detectan. Con frame=0.93 * weight=1.45 = 1.3485 vs
    # SEARCH_INFO frame=0.75 * weight=1.40 = 1.05. Margen: +0.30.
    _Frame.ASK_MEMORY:     [(SemanticIntent.MEMORY_LOOKUP, 1.45), (SemanticIntent.IDENTITY_QUERY, 0.3)],
    _Frame.STORE_DATA:     [(SemanticIntent.MEMORY_LOOKUP, 0.7), (SemanticIntent.GENERAL_CHAT, 0.3)],
    _Frame.COMMAND:        [(SemanticIntent.SYSTEM_ACTION, 1.0)],
    _Frame.GREETING:       [(SemanticIntent.GENERAL_CHAT, 1.0)],
    # STATEMENT → MEMORY_LOOKUP subido de 0.4 → 0.55 para que enunciados
    # personales compitan mejor contra SEARCH_INFO cuando hay ambigüedad.
    _Frame.STATEMENT:      [(SemanticIntent.GENERAL_CHAT, 1.2), (SemanticIntent.MEMORY_LOOKUP, 0.55)],
    _Frame.REQUEST_ACTION: [(SemanticIntent.SYSTEM_ACTION, 0.8), (SemanticIntent.WEB_SEARCH, 0.2)],
}


def _score_intents(
    frames: List[Tuple[_Frame, float]],
    entities: List[Entity],
) -> Dict[SemanticIntent, float]:
    """Puntúa cada intent basándose en frames detectados + entidades."""
    scores: Dict[SemanticIntent, float] = {intent: 0.0 for intent in SemanticIntent}

    # — Contribución de frames —
    for frame, frame_conf in frames:
        intent_weights = _FRAME_INTENT_MAP.get(frame, [])
        for intent, weight in intent_weights:
            scores[intent] += frame_conf * weight

    # — Contribución de entidades —
    entity_types = {e.type for e in entities}

    if "category" in entity_types:
        scores[SemanticIntent.PLACE_SEARCH] += 0.4
    if "location" in entity_types:
        loc_entities = [e for e in entities if e.type == "location"]
        has_nearby = any(e.value == "nearby" for e in loc_entities)
        has_city = any(e.value != "nearby" for e in loc_entities)
        if has_nearby:
            scores[SemanticIntent.PLACE_SEARCH] += 0.3
        if has_city and "category" in entity_types:
            scores[SemanticIntent.PLACE_SEARCH] += 0.2
    if "person" in entity_types:
        person_entities = [e for e in entities if e.type == "person"]
        if any(e.value == "self" for e in person_entities):
            scores[SemanticIntent.IDENTITY_QUERY] += 0.3
            scores[SemanticIntent.MEMORY_LOOKUP] += 0.2
    if "time" in entity_types:
        time_entities = [e for e in entities if e.type == "time"]
        if any(e.value == "operating_now" for e in time_entities):
            scores[SemanticIntent.PLACE_SEARCH] += 0.15

    # — Penalización: si no hay frame de búsqueda de lugar pero sí categoría,
    #   reducir place_search (evita "trabajo en un banco" → place_search).
    #   Penalización atenuada si hay entidad de categoría + verbos de búsqueda
    #   para no matar consultas legítimas que escaparon los frame patterns. —
    if not any(f == _Frame.SEARCH_PLACE for f, _ in frames):
        has_category = "category" in {e.type for e in entities}
        if has_category:
            scores[SemanticIntent.PLACE_SEARCH] *= 0.7
        else:
            scores[SemanticIntent.PLACE_SEARCH] *= 0.3

    return scores


# ---------------------------------------------------------------------------
# Training Examples — calibración interna del clasificador
# ---------------------------------------------------------------------------

TRAINING_EXAMPLES: List[Dict[str, Any]] = [
    {
        "text": "búscame un italiano en Miami",
        "expected_intent": SemanticIntent.PLACE_SEARCH,
        "expected_entities": ["category:italian_restaurant", "location:Miami"],
    },
    {
        "text": "dónde puedo cenar cerca",
        "expected_intent": SemanticIntent.PLACE_SEARCH,
        "expected_entities": ["category:restaurant", "location:nearby"],
    },
    {
        "text": "qué sabes de mí",
        "expected_intent": SemanticIntent.MEMORY_LOOKUP,
        "expected_entities": ["person:self"],
    },
    {
        "text": "qué pasó hoy en Miami",
        "expected_intent": SemanticIntent.WEB_SEARCH,
        "expected_entities": ["time:today", "location:Miami"],
    },
    {
        "text": "recuérdame lo que te dije ayer",
        "expected_intent": SemanticIntent.MEMORY_LOOKUP,
        "expected_entities": ["time:yesterday", "action:remember"],
    },
    {
        "text": "quiero un abogado de divorcio cerca",
        "expected_intent": SemanticIntent.PLACE_SEARCH,
        "expected_entities": ["category:lawyer", "location:nearby"],
    },
    {
        "text": "busca un mecánico abierto ahora",
        "expected_intent": SemanticIntent.PLACE_SEARCH,
        "expected_entities": ["category:car_repair", "time:operating_now", "action:search"],
    },
    # — Negativos: no deben activar place_search —
    {
        "text": "trabajo en un banco desde hace años",
        "expected_intent": SemanticIntent.GENERAL_CHAT,
        "expected_entities": [],
    },
    {
        "text": "me gusta la comida italiana",
        "expected_intent": SemanticIntent.GENERAL_CHAT,
        "expected_entities": ["preference:la comida italiana"],
    },
    {
        "text": "quién soy",
        "expected_intent": SemanticIntent.IDENTITY_QUERY,
        "expected_entities": ["person:self"],
    },
    {
        "text": "explica qué es blockchain",
        "expected_intent": SemanticIntent.WEB_SEARCH,
        "expected_entities": ["action:explain"],
    },
    {
        "text": "crea un módulo de autenticación",
        "expected_intent": SemanticIntent.SYSTEM_ACTION,
        "expected_entities": ["action:create"],
    },
    {
        "text": "hola",
        "expected_intent": SemanticIntent.GENERAL_CHAT,
        "expected_entities": [],
    },
    {
        "text": "qué restaurantes hay abiertos cerca de mí",
        "expected_intent": SemanticIntent.PLACE_SEARCH,
        "expected_entities": ["category:restaurant", "location:nearby", "time:operating_now"],
    },
    {
        "text": "cuáles son mis preferencias",
        "expected_intent": SemanticIntent.MEMORY_LOOKUP,
        "expected_entities": ["person:self"],
    },
    {
        "text": "busca noticias sobre inteligencia artificial",
        "expected_intent": SemanticIntent.WEB_SEARCH,
        "expected_entities": ["action:search"],
    },
    {
        "text": "/status",
        "expected_intent": SemanticIntent.SYSTEM_ACTION,
        "expected_entities": [],
    },
    {
        "text": "best coffee shop in New York",
        "expected_intent": SemanticIntent.PLACE_SEARCH,
        "expected_entities": ["category:cafe", "location:New York"],
    },
]


# ---------------------------------------------------------------------------
# SemanticClassifier
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Embedding-based intent reference vectors
# ---------------------------------------------------------------------------
# Pre-computed at first use. Each intent has a short description that gets
# embedded. When regex frames produce low confidence, we compare the user
# message embedding against these references for a boost.

_INTENT_DESCRIPTIONS: Dict[str, str] = {
    "place_search":   "buscar un lugar físico, restaurante, tienda, farmacia, negocio cercano",
    "web_search":     "buscar información en internet, qué es algo, explicar un concepto, noticias",
    "memory_lookup":  "qué recuerdas de mí, qué te dije, mi historial, mis datos personales",
    "identity_query": "quién soy, cuál es mi nombre, me conoces, mi identidad",
    "general_chat":   "conversación casual, saludo, nota personal, afirmación",
    "system_action":  "ejecutar comando, crear, modificar, desplegar, configurar",
    "market_query":   "precio de bitcoin, cotización de ethereum, tendencia de mercado crypto, análisis btc",
}

_intent_embeddings: Optional[Dict[str, Any]] = None


def _get_intent_embeddings() -> Optional[Dict[str, Any]]:
    """Lazy-load intent reference embeddings. Returns None if model unavailable."""
    global _intent_embeddings
    if _intent_embeddings is not None:
        return _intent_embeddings
    try:
        from vectrax.embeddings import get_embedder
        model = get_embedder()
        import numpy as np
        texts = list(_INTENT_DESCRIPTIONS.values())
        keys = list(_INTENT_DESCRIPTIONS.keys())
        vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        _intent_embeddings = {
            k: np.asarray(v, dtype=np.float32) for k, v in zip(keys, vecs)
        }
        logger.info("Intent embeddings loaded (%d intents)", len(_intent_embeddings))
        return _intent_embeddings
    except Exception as exc:
        logger.debug("Intent embeddings unavailable: %s", exc)
        return None


# 1-slot cache: reutiliza el embedding del último mensaje para que el boost de
# intención y la detección de dominio no encodeen dos veces el mismo texto.
_last_embed_text: str = ""
_last_embed_vec: Any = None


def _embed_query(text: str) -> Optional[Any]:
    """Embed `text` una sola vez (caché de 1 ranura). Devuelve un vector
    np.float32 o None si el modelo no está disponible. Nunca lanza."""
    global _last_embed_text, _last_embed_vec
    if text and text == _last_embed_text and _last_embed_vec is not None:
        return _last_embed_vec
    try:
        from vectrax.embeddings import embed
        import numpy as np
        vec = np.asarray(embed(text), dtype=np.float32)
        _last_embed_text, _last_embed_vec = text, vec
        return vec
    except Exception as exc:
        logger.debug("query embed failed: %s", exc)
        return None


def _embedding_boost(
    text: str,
    scores: Dict[SemanticIntent, float],
) -> Dict[SemanticIntent, float]:
    """Boost intent scores using embedding similarity when regex confidence is low.

    Only activates when the top score produces confidence < 0.5 (below the
    SmartRouter threshold). Adds up to 0.4 to the most similar intent.
    """
    intent_vecs = _get_intent_embeddings()
    if intent_vecs is None:
        return scores

    # Only boost when confidence is low
    max_score = max(scores.values()) if scores else 0
    if max_score / 2.0 >= 0.5:  # already above threshold
        return scores

    try:
        import numpy as np
        query_vec = _embed_query(text)
        if query_vec is None:
            return scores

        best_intent = None
        best_sim = 0.0
        for intent_key, ref_vec in intent_vecs.items():
            sim = float(np.dot(query_vec, ref_vec))
            if sim > best_sim:
                best_sim = sim
                best_intent = intent_key

        if best_intent and best_sim > 0.3:
            # Map string key back to SemanticIntent enum
            intent_enum = {
                "place_search": SemanticIntent.PLACE_SEARCH,
                "web_search": SemanticIntent.WEB_SEARCH,
                "memory_lookup": SemanticIntent.MEMORY_LOOKUP,
                "identity_query": SemanticIntent.IDENTITY_QUERY,
                "general_chat": SemanticIntent.GENERAL_CHAT,
                "system_action": SemanticIntent.SYSTEM_ACTION,
            }.get(best_intent)
            if intent_enum:
                boost = min(0.4, best_sim * 0.5)
                scores[intent_enum] = scores.get(intent_enum, 0) + boost
                logger.debug(
                    "Embedding boost: %s +%.2f (sim=%.3f)",
                    best_intent, boost, best_sim,
                )
    except Exception as exc:
        logger.debug("Embedding boost failed: %s", exc)

    return scores


# ---------------------------------------------------------------------------
# Domain detection — dominio de conocimiento de la consulta
# ---------------------------------------------------------------------------
# El motor de intención ya entiende la oración completa; su salida se extiende
# para incluir también el DOMINIO de conocimiento de la consulta
# (market, freight_logistics, florida_real_estate, cybersecurity, …), en vez
# de depender de un extractor de tema posicional separado y más frágil.
#
# Los dominios son DINÁMICOS: provienen de criterion.known_domains()
# (gravity + domain_library), no de un enum fijo, así el clasificador se
# auto-adapta cuando nace un dominio nuevo, sin tocar código.
#
# Estrategia híbrida (precisión + robustez, coste acotado):
#   1) Fast-path léxico determinista (criterion.detect_domain): alta precisión
#      cuando hay clave/vocabulario de dominio en el texto. Muy barato.
#   2) Fallback semántico por embeddings: compara el embedding de la consulta
#      contra un prototipo por dominio (nombre + vocabulario), reutilizando el
#      MISMO vector ya calculado para el boost de intención (sin doble encode).
# Degrada con gracia: sin dominios o sin modelo → ("", 0.0). Nunca lanza.

_SEMANTIC_DOMAIN_ENABLED = os.getenv("VX_SEMANTIC_DOMAIN", "1") not in ("0", "false", "False", "")
try:
    _SEMANTIC_DOMAIN_FLOOR = float(os.getenv("VX_SEMANTIC_DOMAIN_FLOOR", "0.45"))
except (TypeError, ValueError):
    _SEMANTIC_DOMAIN_FLOOR = 0.45
_KEYWORD_DOMAIN_CONFIDENCE = 0.9

_domain_embeddings: Optional[Dict[str, Any]] = None
_domain_embeddings_key: str = ""


def _domain_prototype_text(domain: str, vocab: Tuple[str, ...]) -> str:
    """Texto prototipo de un dominio: nombre humanizado + su vocabulario."""
    name = domain.replace("_", " ").strip()
    terms = " ".join(list(vocab)[:24])
    return (name + " " + terms).strip()


def _get_domain_embeddings() -> Optional[Dict[str, Any]]:
    """Lazy-load de embeddings prototipo por dominio. Se reconstruye si el
    conjunto de dominios cambia (cache-key = dominios ordenados). Devuelve None
    si no hay dominios conocidos o el modelo no está disponible."""
    global _domain_embeddings, _domain_embeddings_key
    try:
        from core.learn.criterion import known_domains, _DOMAIN_VOCAB
    except Exception as exc:
        logger.debug("domain vocab unavailable: %s", exc)
        return None
    try:
        domains = known_domains()
    except Exception as exc:
        logger.debug("known_domains error: %s", exc)
        return None
    if not domains:
        return None
    key = "|".join(domains)
    if _domain_embeddings is not None and _domain_embeddings_key == key:
        return _domain_embeddings
    try:
        from vectrax.embeddings import get_embedder
        import numpy as np
        model = get_embedder()
        keys: List[str] = []
        texts: List[str] = []
        for d in domains:
            keys.append(d)
            texts.append(_domain_prototype_text(d, _DOMAIN_VOCAB.get(d, ())))
        vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        _domain_embeddings = {
            k: np.asarray(v, dtype=np.float32) for k, v in zip(keys, vecs)
        }
        _domain_embeddings_key = key
        logger.info("Domain embeddings loaded (%d domains)", len(_domain_embeddings))
        return _domain_embeddings
    except Exception as exc:
        logger.debug("domain embeddings unavailable: %s", exc)
        return None


def _detect_domain(text: str) -> Tuple[str, float]:
    """Detecta el DOMINIO de conocimiento de `text` → (domain, confidence).

    Híbrido: léxico determinista primero (barato, preciso); si no hay match,
    fallback semántico por embeddings (reutiliza el vector cacheado del boost).
    Devuelve ("", 0.0) si no hay dominio claro. Totalmente defensivo.
    """
    if not text:
        return "", 0.0

    # 1) Fast-path léxico determinista (clave/vocabulario de dominio).
    try:
        from core.learn.criterion import detect_domain as _kw_detect
        kw = _kw_detect(text)
        if kw:
            return kw, _KEYWORD_DOMAIN_CONFIDENCE
    except Exception as exc:
        logger.debug("keyword detect_domain failed: %s", exc)

    # 2) Fallback semántico (opcional, reutiliza el embedding de la consulta).
    if not _SEMANTIC_DOMAIN_ENABLED:
        return "", 0.0
    dom_vecs = _get_domain_embeddings()
    if not dom_vecs:
        return "", 0.0
    query_vec = _embed_query(text)
    if query_vec is None:
        return "", 0.0
    try:
        import numpy as np
        best = ""
        best_sim = 0.0
        for dom, ref in dom_vecs.items():
            sim = float(np.dot(query_vec, ref))
            if sim > best_sim:
                best_sim = sim
                best = dom
        if best and best_sim >= _SEMANTIC_DOMAIN_FLOOR:
            return best, round(best_sim, 4)
    except Exception as exc:
        logger.debug("semantic domain detection failed: %s", exc)
    return "", 0.0


class SemanticClassifier:
    """
    Clasificador semántico central de Vectrax.

    Analiza la intención del usuario en 4+1 fases:
      1. Frame detection (estructura sintáctica)
      2. Entity extraction (entidades con contexto)
      3. Intent scoring (puntuación contextual)
      4. Embedding boost (si confianza < 0.5, usa similaridad semántica)
      5. Domain detection (dominio de conocimiento de la consulta)

    Usage::

        classifier = SemanticClassifier()
        result = classifier.classify("búscame un italiano en Miami")
        print(result.intent)            # → place_search
        print(result.entities)          # → [category:italian_restaurant, location:Miami]
        print(result.suggested_tools)   # → ["google_places", "location_api"]
    """

    def classify(self, text: str) -> SemanticResult:
        """
        Clasifica un mensaje de usuario en una intención semántica.

        Args:
            text: Mensaje del usuario en lenguaje natural.

        Returns:
            SemanticResult con intent, entidades, herramientas sugeridas.
        """
        if not text or not text.strip():
            return SemanticResult(
                intent=SemanticIntent.GENERAL_CHAT,
                confidence=0.1,
                frame="empty",
            )

        stripped = text.strip()

        # Phase 1: Frame Detection
        frames = _detect_frames(stripped)

        # Phase 2: Entity Extraction
        entities = _extract_entities(stripped, frames)

        # Phase 3: Intent Scoring
        scores = _score_intents(frames, entities)

        # Phase 4: Embedding boost (when regex confidence is low)
        scores = _embedding_boost(stripped, scores)

        # Phase 5: Domain detection. El mismo motor que entiende la intención
        # expone también el dominio de conocimiento, en vez de depender de un
        # extractor de tema posicional aparte.
        domain, domain_conf = _detect_domain(stripped)

        # Seleccionar intent principal
        if not scores or all(v == 0.0 for v in scores.values()):
            # Sin señales → general_chat con baja confianza
            return SemanticResult(
                intent=SemanticIntent.GENERAL_CHAT,
                confidence=0.3,
                entities=entities,
                suggested_tools=_INTENT_TOOLS[SemanticIntent.GENERAL_CHAT],
                frame="none",
                scores={k.value: round(v, 4) for k, v in scores.items()},
                domain=domain,
                domain_confidence=domain_conf,
            )

        ranked = sorted(scores.items(), key=lambda x: -x[1])
        best_intent, best_score = ranked[0]

        # Normalizar confianza (0.0 – 1.0)
        max_possible = 2.0  # máximo razonable con frame + entidades
        confidence = min(1.0, best_score / max_possible)

        # Detectar intents secundarios (score > 30% del principal)
        threshold = best_score * 0.3
        secondary = [
            intent for intent, score in ranked[1:]
            if score > threshold and score > 0.1
        ]

        # Si hay múltiples intents fuertes y confianza baja → MIXED
        if len(secondary) >= 2 and confidence < 0.5:
            best_intent = SemanticIntent.MIXED
            confidence = max(confidence, 0.35)

        # Frame principal (para logging)
        primary_frame = frames[0][0].value if frames else "none"

        # Herramientas sugeridas
        tools = _INTENT_TOOLS.get(best_intent, ["llm"])

        result = SemanticResult(
            intent=best_intent,
            secondary_intents=secondary[:3],
            confidence=round(confidence, 4),
            entities=entities,
            suggested_tools=tools,
            frame=primary_frame,
            scores={k.value: round(v, 4) for k, v in scores.items()},
            domain=domain,
            domain_confidence=domain_conf,
        )

        logger.info(
            "SemanticClassifier: intent=%s conf=%.2f frame=%s entities=%d",
            result.intent.value, result.confidence,
            result.frame, len(result.entities),
        )
        return result


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_classifier: Optional[SemanticClassifier] = None


def get_semantic_classifier() -> SemanticClassifier:
    """Return the global SemanticClassifier singleton."""
    global _classifier
    if _classifier is None:
        _classifier = SemanticClassifier()
    return _classifier


def classify(text: str) -> SemanticResult:
    """Shortcut: clasifica un texto usando el singleton."""
    return get_semantic_classifier().classify(text)
