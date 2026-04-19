"""
Vectrax — Place Search Integration
======================================
Módulo de búsqueda de lugares reales usando Google Places API (New).

Funcionalidades:
  - Text Search: búsqueda abierta por texto ("farmacia en Santiago")
  - Nearby Search: búsqueda por cercanía cuando hay ubicación
  - Place Details: ampliación de datos (teléfono, horarios)
  - Detección de intención de búsqueda de lugar
  - Formateo de resultados en estilo Vectrax

Uso:
  from vectrax.integrations.place_search import search_places, detect_place_intent

  if detect_place_intent(user_message):
      results = search_places(user_message, user_location={"lat": -33.45, "lng": -70.66})

Variable de entorno requerida: GOOGLE_PLACES_API_KEY

Capa: External — Integración
Creado: 2026-03-19
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger("vectrax.integrations.place_search")

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

# Legacy Places API endpoints
PLACES_API_BASE = "https://maps.googleapis.com/maps/api/place"
DEFAULT_LANGUAGE = "es"
DEFAULT_MAX_RESULTS = 3  # máximo 3 — no explicar, solo resolver
DEFAULT_RADIUS_METERS = 5000  # 5 km para búsquedas nearby

# ---------------------------------------------------------------------------
# Palabras clave para detección de intención de lugar
# ---------------------------------------------------------------------------

# Tipos de lugar (español e inglés)
PLACE_KEYWORDS: set[str] = {
    # Español
    "restaurante", "restaurant", "taller", "mecánico", "mecanico",
    "farmacia", "supermercado", "gasolinera", "tienda", "banco",
    "cajero", "hospital", "dentista", "ferretería", "ferreteria",
    "veterinario", "veterinaria", "hotel", "gimnasio", "barbería",
    "barberia", "cafetería", "cafeteria", "comida", "panadería",
    "panaderia", "lavandería", "lavanderia", "estacionamiento",
    "parking", "parque", "plaza", "centro comercial", "mall",
    "clínica", "clinica", "óptica", "optica", "zapatería",
    "zapateria", "librería", "libreria", "papelería", "papeleria",
    "peluquería", "peluqueria", "carnicería", "carniceria",
    "verdulería", "verduleria", "pollería", "polleria",
    "pizzería", "pizzeria", "heladería", "heladeria",
    "bar", "pub", "discoteca", "cine", "teatro", "museo",
    "iglesia", "templo", "mezquita", "sinagoga",
    "escuela", "colegio", "universidad", "instituto",
    "oficina", "notaría", "notaria", "correo", "posta",
    "bomberos", "comisaría", "comisaria", "policía", "policia",
    "aeropuerto", "terminal", "estación", "estacion",
    "tintorería", "tintoreria", "cerrajero", "cerrajería",
    "electricista", "plomero", "fontanero", "carpintero",
    "spa", "sauna", "piscina", "cancha",
    # Inglés
    "pharmacy", "supermarket", "gas station", "store", "shop",
    "bank", "atm", "gym", "barber", "cafe", "coffee",
    "bakery", "laundry", "clinic", "school", "airport",
    "hardware store", "pet store", "bookstore",
}

# Patrones que indican búsqueda de lugar (frases)
PLACE_INTENT_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"(?:d[oó]nde|donde)\s+(?:hay|está|esta|queda|encuentro|puedo|consigo|compro|venden)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:cu[aá]l|cual)\s+(?:es\s+)?(?:el|la|los|las)\s+(?:\w+\s+)?(?:m[aá]s\s+cercan[oa]|cerca)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:qu[eé]|que)\s+(?:hay|lugar|sitio|local)\s+(?:cerca|cercano|por aquí|por aqui|hay cerca)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:qu[eé]|que)\s+(?:lugar|sitio|local)\s+\w+\s+(?:cerca|cercano|cercana|por aquí|por aqui)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:busco|necesito|quiero)\s+(?:un|una|el|la)\s+\w+\s+(?:cerca|cercano|cercana|por aquí|aquí cerca)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:cerca\s+de\s+(?:mí|mi|aquí|aqui|acá|aca))",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:near\s*(?:me|by|here)|closest|nearby)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:d[oó]nde|donde)\s+(?:compro|como|como|reparo|arreglo|lavo|imprimo)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:hay\s+(?:un|una|algún|alguna)\s+\w+\s+(?:cerca|aquí|por aquí|abierto|abierta))",
        re.IGNORECASE,
    ),
    # --- Patrones ampliados: búsquedas naturales ---
    re.compile(
        r"\b(?:busca|buscar|encuentra|encontrar|search|find)\b.*\b(?:restaurant|restaurante|farmacia|hotel|tienda|banco|taller|cafe|bar|hospital|gym|gimnasio|pharmacy|store|shop)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:restaurant|restaurante|farmacia|hotel|tienda|banco|taller|cafe|bar|hospital|gym|gimnasio|pharmacy|store|shop)s?\b.*\b(?:en|in|at)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:mejores|best|top|buenos|buenas)\b.*\b(?:restaurant|restaurante|farmacia|hotel|tienda|cafe|bar|gym|gimnasio)s?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:busca|buscar|encuentra|encontrar)\b.*\b(?:en|in)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:google\s*places|places)\b",
        re.IGNORECASE,
    ),
]


# ---------------------------------------------------------------------------
# Detección de intención
# ---------------------------------------------------------------------------

def detect_place_intent(message: str) -> bool:
    """
    Detecta si el mensaje del usuario tiene intención de buscar un lugar
    físico, negocio, tienda, servicio o punto de interés.

    Returns:
        True si el mensaje indica búsqueda de lugar.
    """
    if not message or not message.strip():
        return False

    text = message.strip().lower()

    # 1. Verificar patrones de frase completa
    for pattern in PLACE_INTENT_PATTERNS:
        if pattern.search(text):
            return True

    # 2. Verificar keywords de tipo de lugar
    for keyword in PLACE_KEYWORDS:
        # Buscar como palabra completa (no substring de otra palabra)
        if re.search(rf"\b{re.escape(keyword)}\b", text):
            # Verificar que haya contexto de búsqueda (no solo mención informativa)
            if _has_search_context(text):
                return True

    # 3. Verificar contexto emocional/situacional
    ctx_type, _ = _detect_context_place(text)
    if ctx_type:
        return True

    return False


def _has_search_context(text: str) -> bool:
    """
    Verifica que el texto tenga contexto de búsqueda de lugar,
    no solo una mención casual.

    Ejemplos positivos: "busco un taller", "dónde hay farmacia",
                        "restaurantes en Miami", "busca farmacias"
    Ejemplos negativos: "trabajo en un banco" (informativa)
    """
    search_signals = [
        r"\b(?:d[oó]nde|donde|busco|necesito|quiero|hay|encuentra|cerca|cercano|cercana)\b",
        r"\b(?:m[aá]s\s+cercan[oa]|por\s+aqu[ií]|aqu[ií]\s+cerca)\b",
        r"\b(?:near|closest|nearby|find|search|looking\s+for)\b",
        r"\b(?:abierto|abierta|horario|dirección|direccion|teléfono|telefono)\b",
        r"\b(?:recomienda|recomendación|mejor|mejores|bueno|buena|buenos|buenas|barato|económico|top|best)\b",
        r"\b(?:busca|buscar|encuentra|encontrar|search|find)\b",
        r"\b(?:en|in)\s+[A-ZÁÉÍÓÚ]",  # "en Miami", "in New York" (ciudad con mayúscula)
        r"\b(?:rating|estrellas|calificación|reviews|reseñas|puntuación)\b",
        r"\b(?:google\s*places|places)\b",
        r"\?",  # Pregunta implícita
    ]
    for signal in search_signals:
        if re.search(signal, text, re.IGNORECASE):
            return True
    return False


def _wants_nearby(text: str) -> bool:
    """Detecta si el usuario quiere resultados por cercanía."""
    nearby_patterns = [
        r"\b(?:cerca|cercano|cercana|cercan[oa]s)\b",
        r"\b(?:por\s+aqu[ií]|aqu[ií]\s+cerca|de\s+aqu[ií])\b",
        r"\b(?:near\s*(?:me|by|here)|closest|nearby)\b",
        r"\b(?:m[aá]s\s+cercan[oa]|m[aá]s\s+cerca)\b",
        r"\b(?:cerca\s+de\s+m[ií]|cerca\s+de\s+ac[aá])\b",
    ]
    for pattern in nearby_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _extract_search_query(message: str) -> str:
    """
    Extrae la consulta de búsqueda limpia del mensaje del usuario.
    Elimina frases de contexto para dejar solo el tipo de lugar.
    """
    text = message.strip()

    # Eliminar frases de contexto que no aportan a la búsqueda en API
    remove_patterns = [
        r"(?:d[oó]nde|donde)\s+(?:hay|está|esta|queda|encuentro|puedo encontrar|consigo|compro|venden)\s+",
        r"(?:cu[aá]l|cual)\s+(?:es\s+)?(?:el|la|los|las)\s+",
        r"(?:qu[eé]|que)\s+(?:hay|lugar|sitio|local)\s+",
        r"(?:busco|necesito|quiero)\s+(?:un|una|el|la)\s+",
        r"(?:m[aá]s\s+cercan[oa])\s*",
        r"(?:cerca\s+de\s+(?:mí|mi|aquí|aqui|acá|aca))\s*",
        r"(?:near\s*(?:me|by|here))\s*",
        r"(?:por\s+aqu[ií]|aqu[ií]\s+cerca)\s*",
        r"\?+\s*$",
    ]

    cleaned = text
    for pattern in remove_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()

    # Si la limpieza dejó el texto vacío, usar el original
    if not cleaned or len(cleaned) < 3:
        cleaned = text

    return cleaned


# ---------------------------------------------------------------------------
# API — Google Places (Legacy)
# ---------------------------------------------------------------------------
# Usa la Places API legacy (no la "New") con endpoints GET estándar.
# Se controla el costo con el parámetro 'fields' para pedir solo los
# campos necesarios.
#
# Campos básicos (sin costo extra):
#   name, formatted_address, geometry, place_id, types, business_status
#
# Campos de contacto ($):
#   formatted_phone_number, opening_hours, website
#
# Campos de ambiente ($$):
#   rating, user_ratings_total, reviews, price_level
# ---------------------------------------------------------------------------

# Fields para búsquedas (Text Search / Nearby Search)
# Nota: Text Search legacy no soporta 'fields' — devuelve todo.
# Nearby Search sí soporta 'fields' pero es opcional.

# Fields para Place Details — básico (sin costo extra)
_DETAILS_FIELDS_BASIC = "name,formatted_address,geometry,place_id,types"

# Fields para Place Details — completo (contacto + rating)
_DETAILS_FIELDS_FULL = (
    "name,formatted_address,geometry,place_id,types,"
    "rating,user_ratings_total,formatted_phone_number,"
    "opening_hours,website,url"
)


def _get_api_key() -> str:
    """Obtiene la API key de Google Places."""
    key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
    if not key:
        logger.warning("GOOGLE_PLACES_API_KEY no configurada")
    return key


def _text_search(
    query: str,
    language: str = DEFAULT_LANGUAGE,
    max_results: int = DEFAULT_MAX_RESULTS,
    location_bias: Optional[Dict[str, float]] = None,
    included_type: str = "",
    include_rating: bool = True,
) -> List[Dict[str, Any]]:
    """
    Google Places Text Search (Legacy API).
    GET https://maps.googleapis.com/maps/api/place/textsearch/json

    Args:
        query: Texto de búsqueda.
        language: Código de idioma.
        max_results: Máximo de resultados (hasta 20 por página).
        location_bias: Centro para sesgar resultados.
        included_type: Tipo de lugar (ej: "restaurant", "pharmacy").
        include_rating: No aplica en legacy (siempre incluye rating).
    """
    api_key = _get_api_key()
    if not api_key:
        return []

    url = f"{PLACES_API_BASE}/textsearch/json"

    params: Dict[str, Any] = {
        "query": query,
        "language": language,
        "key": api_key,
    }

    if included_type:
        params["type"] = included_type

    if location_bias:
        params["location"] = f"{location_bias['lat']},{location_bias['lng']}"
        params["radius"] = DEFAULT_RADIUS_METERS

    try:
        resp = httpx.get(url, params=params, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "")
        if status not in ("OK", "ZERO_RESULTS"):
            logger.error("Places Text Search status: %s — %s",
                         status, data.get("error_message", ""))
            return []
        return data.get("results", [])[:max_results]
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Places Text Search HTTP error: %s %s",
            exc.response.status_code, exc.response.text[:200],
        )
        return []
    except Exception as exc:
        logger.error("Places Text Search failed: %s", exc)
        return []


def _nearby_search(
    location: Dict[str, float],
    included_types: Optional[List[str]] = None,
    radius: float = DEFAULT_RADIUS_METERS,
    max_results: int = DEFAULT_MAX_RESULTS,
    language: str = DEFAULT_LANGUAGE,
    include_rating: bool = True,
) -> List[Dict[str, Any]]:
    """
    Google Places Nearby Search (Legacy API).
    GET https://maps.googleapis.com/maps/api/place/nearbysearch/json

    Args:
        location: Dict con "lat" y "lng".
        included_types: Lista de tipos (usa el primero como 'type').
        radius: Radio en metros.
        max_results: Máximo de resultados.
        language: Código de idioma.
        include_rating: No aplica en legacy.
    """
    api_key = _get_api_key()
    if not api_key:
        return []

    url = f"{PLACES_API_BASE}/nearbysearch/json"

    params: Dict[str, Any] = {
        "location": f"{location['lat']},{location['lng']}",
        "radius": int(radius),
        "language": language,
        "key": api_key,
    }

    if included_types:
        params["type"] = included_types[0]

    try:
        resp = httpx.get(url, params=params, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "")
        if status not in ("OK", "ZERO_RESULTS"):
            logger.error("Places Nearby Search status: %s — %s",
                         status, data.get("error_message", ""))
            return []
        return data.get("results", [])[:max_results]
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Places Nearby Search HTTP error: %s %s",
            exc.response.status_code, exc.response.text[:200],
        )
        return []
    except Exception as exc:
        logger.error("Places Nearby Search failed: %s", exc)
        return []


def _get_place_details(
    place_id: str,
    language: str = DEFAULT_LANGUAGE,
    full: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Google Places Details (Legacy API).
    GET https://maps.googleapis.com/maps/api/place/details/json

    Args:
        place_id: ID del lugar (ej: "ChIJ...").
        language: Código de idioma.
        full: Si True, incluye contacto y horarios.
    """
    api_key = _get_api_key()
    if not api_key:
        return None

    url = f"{PLACES_API_BASE}/details/json"

    fields = _DETAILS_FIELDS_FULL if full else _DETAILS_FIELDS_BASIC

    params = {
        "place_id": place_id,
        "fields": fields,
        "language": language,
        "key": api_key,
    }

    try:
        resp = httpx.get(url, params=params, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "OK":
            logger.error("Place Details status: %s", data.get("status", ""))
            return None
        return data.get("result")
    except Exception as exc:
        logger.error("Place Details failed for %s: %s", place_id, exc)
        return None


# ---------------------------------------------------------------------------
# Distancia aproximada
# ---------------------------------------------------------------------------

def _approx_distance_km(
    lat1: float, lng1: float, lat2: float, lng2: float,
) -> float:
    """Distancia aproximada en km usando fórmula de Haversine simplificada."""
    import math
    R = 6371  # Radio tierra en km
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlng / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def _distance_label(km: float) -> str:
    """Convierte km a etiqueta legible."""
    if km < 1:
        meters = int(km * 1000)
        return f"{meters} m"
    return f"{km:.1f} km"


# ---------------------------------------------------------------------------
# Normalización de resultados
# ---------------------------------------------------------------------------

def _normalize_place(
    raw: Dict[str, Any],
    user_location: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Normaliza un resultado de la API legacy a estructura Vectrax."""
    name = raw.get("name", "")

    types = raw.get("types", [])
    category = types[0] if types else ""

    place_location = raw.get("geometry", {}).get("location", {})
    place_lat = place_location.get("lat", 0)
    place_lng = place_location.get("lng", 0)

    distance = None
    distance_label = ""
    if user_location and place_lat and place_lng:
        distance = _approx_distance_km(
            user_location["lat"], user_location["lng"],
            place_lat, place_lng,
        )
        distance_label = _distance_label(distance)

    return {
        "nombre": name,
        "categoria": category,
        "rating": raw.get("rating"),
        "total_reviews": raw.get("user_ratings_total"),
        "direccion": raw.get("formatted_address", ""),
        "distancia_km": round(distance, 2) if distance is not None else None,
        "distancia_label": distance_label,
        "place_id": raw.get("place_id", ""),
        "lat": place_lat,
        "lng": place_lng,
    }


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def search_places(
    user_query: str,
    user_location: Optional[Dict[str, float]] = None,
    max_results: int = DEFAULT_MAX_RESULTS,
    language: str = DEFAULT_LANGUAGE,
) -> Dict[str, Any]:
    """
    Busca lugares reales basándose en la consulta del usuario.

    Args:
        user_query: Mensaje del usuario (ej: "farmacia cerca de mí").
        user_location: Dict con "lat" y "lng" del usuario. Opcional.
        max_results: Máximo de resultados a devolver.
        language: Código de idioma para resultados.

    Returns:
        Dict con:
          - "found": bool
          - "results": List[Dict] con lugares normalizados
          - "query_used": str consulta enviada a la API
          - "search_type": "nearby" | "text"
          - "message": str respuesta formateada para el usuario
    """
    if not user_query or not user_query.strip():
        return {
            "found": False,
            "results": [],
            "query_used": "",
            "search_type": "",
            "message": "No se proporcionó una consulta de búsqueda.",
        }

    api_key = _get_api_key()
    if not api_key:
        return {
            "found": False,
            "results": [],
            "query_used": user_query,
            "search_type": "",
            "message": "Búsqueda de lugares no disponible en este momento.",
        }

    text = user_query.strip()
    search_query = _extract_search_query(text)
    wants_near = _wants_nearby(text)
    detected_type = _detect_place_type(text)
    raw_places: List[Dict[str, Any]] = []
    search_type = "text"

    # Estrategia: Nearby cuando hay ubicación (siempre — no solo cuando dice "cerca")
    if user_location:
        search_type = "nearby"
        raw_places = _nearby_search(
            location=user_location,
            included_types=[detected_type] if detected_type else None,
            max_results=max_results,
            language=language,
        )
        # Fallback a text search con location bias si nearby no dio resultados
        if not raw_places:
            search_type = "text"
            raw_places = _text_search(
                query=search_query,
                max_results=max_results,
                language=language,
                location_bias=user_location,
                included_type=detected_type,
            )
    else:
        # Sin ubicación: text search puro
        raw_places = _text_search(
            query=search_query,
            max_results=max_results,
            language=language,
            included_type=detected_type,
        )

    # Normalizar resultados
    results = [
        _normalize_place(p, user_location)
        for p in raw_places
    ]

    # Ordenar por distancia si hay ubicación
    if user_location:
        results.sort(
            key=lambda r: r["distancia_km"] if r["distancia_km"] is not None else 9999,
        )

    found = len(results) > 0
    message = format_results(results, search_query) if found else _no_results_message(search_query)

    logger.info(
        "Place search: query=%r | type=%s | found=%d | location=%s",
        search_query, search_type, len(results),
        "yes" if user_location else "no",
    )

    return {
        "found": found,
        "results": results,
        "query_used": search_query,
        "search_type": search_type,
        "message": message,
    }


# ---------------------------------------------------------------------------
# Formateo de resultados
# ---------------------------------------------------------------------------

def format_results(results: List[Dict[str, Any]], query: str = "") -> str:
    """
    Formato directo: máximo 3, sin intro, ordenados por distancia.

    Ejemplo:
        Farmacia Cruz Verde — 4.2★ — 200 m
        Farmacia Ahumada — 4.0★ — 350 m
        Salcobrand — 3.8★ — 600 m
    """
    if not results:
        return _no_results_message(query)

    lines = []
    for place in results[:3]:  # máximo 3
        parts = [place['nombre']]

        if place.get("rating"):
            parts.append(f"{place['rating']}★")

        if place.get("distancia_label"):
            parts.append(place["distancia_label"])
        elif place.get("direccion"):
            # Solo mostrar dirección si no hay distancia
            addr = place["direccion"]
            if len(addr) > 50:
                addr = addr[:47] + "..."
            parts.append(addr)

        lines.append(" — ".join(parts))

    return "\n".join(lines)


def _no_results_message(query: str = "") -> str:
    """Mensaje cuando no se encuentran resultados."""
    if query:
        return f"No encontré resultados para \"{query}\". Intenta con otro término o agrega una ciudad."
    return "No encontré resultados. Intenta describir qué tipo de lugar buscas."


# ---------------------------------------------------------------------------
# Mapeo keyword → tipo Google Places
# ---------------------------------------------------------------------------

KEYWORD_TO_PLACE_TYPE: Dict[str, str] = {
    "restaurante": "restaurant",
    "restaurant": "restaurant",
    "comida": "restaurant",
    "farmacia": "pharmacy",
    "pharmacy": "pharmacy",
    "supermercado": "supermarket",
    "supermarket": "supermarket",
    "gasolinera": "gas_station",
    "gas station": "gas_station",
    "banco": "bank",
    "bank": "bank",
    "cajero": "atm",
    "atm": "atm",
    "hospital": "hospital",
    "clínica": "hospital",
    "clinica": "hospital",
    "dentista": "dentist",
    "veterinario": "veterinary_care",
    "veterinaria": "veterinary_care",
    "hotel": "lodging",
    "gimnasio": "gym",
    "gym": "gym",
    "barbería": "hair_care",
    "barberia": "hair_care",
    "peluquería": "hair_care",
    "peluqueria": "hair_care",
    "barber": "hair_care",
    "cafetería": "cafe",
    "cafeteria": "cafe",
    "cafe": "cafe",
    "coffee": "cafe",
    "panadería": "bakery",
    "panaderia": "bakery",
    "bakery": "bakery",
    "lavandería": "laundry",
    "lavanderia": "laundry",
    "laundry": "laundry",
    "cine": "movie_theater",
    "museo": "museum",
    "escuela": "school",
    "colegio": "school",
    "school": "school",
    "universidad": "university",
    "correo": "post_office",
    "aeropuerto": "airport",
    "airport": "airport",
    "estación": "transit_station",
    "estacion": "transit_station",
    "ferretería": "hardware_store",
    "ferreteria": "hardware_store",
    "hardware store": "hardware_store",
    "librería": "book_store",
    "libreria": "book_store",
    "bookstore": "book_store",
    "taller": "car_repair",
    "mecánico": "car_repair",
    "mecanico": "car_repair",
    "spa": "spa",
}


# ---------------------------------------------------------------------------
# Mapeo de contexto emocional/situacional → tipo de lugar
# ---------------------------------------------------------------------------

CONTEXT_TO_PLACE_TYPE: Dict[str, Tuple[str, str]] = {
    # (patrón, tipo_google_places, query_extra para text search)
    # Hambre / comida
    "tengo hambre":                 ("restaurant", "comida rápida"),
    "muero de hambre":              ("restaurant", "comida rápida"),
    "quiero comer":                 ("restaurant", "restaurante"),
    "vamos a comer":                ("restaurant", "restaurante"),
    "comida r[aá]pida":              ("restaurant", "comida rápida fast food"),
    "algo r[aá]pido":                ("restaurant", "comida rápida"),
    "algo para comer":              ("restaurant", "restaurante"),
    "donde como":                   ("restaurant", "restaurante"),
    "d[oó]nde como":                 ("restaurant", "restaurante"),
    # Tranquilidad / relajación
    "algo tranquilo":               ("cafe", "café tranquilo"),
    "lugar tranquilo":              ("cafe", "café tranquilo"),
    "sitio tranquilo":              ("cafe", "café tranquilo"),
    "quiero relajar":               ("spa", "spa relax"),
    "algo relajado":                ("cafe", "café lounge"),
    "un caf[eé]":                    ("cafe", "cafetería"),
    # Diversión / salir
    "quiero salir":                 ("bar", "bar restaurante"),
    "vamos a salir":                ("bar", "bar restaurante"),
    "algo divertido":               ("night_club", "bar discoteca"),
    "donde tomar":                  ("bar", "bar"),
    "tomar algo":                   ("bar", "bar"),
    "una cerveza":                  ("bar", "bar cerveza"),
    # Calidad
    "algo bueno":                   ("restaurant", "mejor restaurante rating"),
    "algo mejor":                   ("restaurant", "mejor restaurante"),
    "un buen restaurante":          ("restaurant", "mejor restaurante"),
    "algo especial":                ("restaurant", "restaurante fino"),
    "cena rom[aá]ntica":              ("restaurant", "restaurante romántico"),
    # Emergencia / necesidad
    "me siento mal":                ("hospital", "urgencias hospital"),
    "necesito un doctor":           ("doctor", "clínica médica"),
    "necesito gasolina":            ("gas_station", "gasolinera"),
    "se me ponch[oó]":               ("car_repair", "taller llantas"),
}


def _detect_context_place(text: str) -> Tuple[str, str]:
    """
    Detecta tipo de lugar a partir del contexto emocional/situacional.

    Returns:
        (google_place_type, search_query_hint) o ("", "") si no detecta.
    """
    text_lower = text.lower().strip()
    for pattern, (place_type, query_hint) in CONTEXT_TO_PLACE_TYPE.items():
        if re.search(pattern, text_lower):
            return place_type, query_hint
    return "", ""


def _detect_place_type(text: str) -> str:
    """Detecta el tipo de Google Places a partir del texto del usuario."""
    text_lower = text.lower()
    # 1. Primero buscar keywords directos
    for keyword, place_type in KEYWORD_TO_PLACE_TYPE.items():
        if re.search(rf"\b{re.escape(keyword)}\b", text_lower):
            return place_type
    # 2. Luego buscar contexto emocional/situacional
    ctx_type, _ = _detect_context_place(text)
    return ctx_type
