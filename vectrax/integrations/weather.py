"""
Vectrax Weather — Clima en tiempo real
==========================================
Obtiene el clima actual y pronóstico usando OpenWeatherMap API.
Se integra con:
  - Scheduler: "dame el clima todas las mañanas" → envía clima real
  - Gateway: "qué temperatura hace en Miami" → respuesta directa

API: OpenWeatherMap (free tier: 60 calls/min, 1M/month)
Signup: https://openweathermap.org/api  →  OPENWEATHER_API_KEY en .env

Si no hay API key, usa Tavily web search como fallback.

Creado: 2026-04-18
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import re
from typing import Dict, Optional

logger = logging.getLogger("vectrax.weather")

_OWM_KEY = os.environ.get("OPENWEATHER_API_KEY", "")
_OWM_BASE = "https://api.openweathermap.org/data/2.5"


def get_weather(
    city: str = "Miami",
    lang: str = "es",
) -> Optional[Dict]:
    """
    Get current weather for a city.

    Returns dict with:
        city, temp, feels_like, description, humidity, wind, icon
    Or None if failed.
    """
    if _OWM_KEY:
        return _owm_weather(city, lang)
    return _fallback_weather(city, lang)


def get_weather_text(
    city: str = "Miami",
    lang: str = "es",
) -> str:
    """
    Get formatted weather text for Telegram.
    Always returns a string (never None).
    """
    data = get_weather(city, lang)
    if not data:
        return f"No pude obtener el clima de {city}."
    return _format_weather(data, lang)


# Words to strip from city names
_TIME_NOISE = re.compile(
    r'\b(?:hoy|ahora|now|today|mañana|tomorrow|esta\s+(?:tarde|noche|semana)|tonight|ahorita)\b',
    re.IGNORECASE,
)

def detect_weather_intent(text: str) -> Optional[str]:
    """
    Detect if text is asking about weather.
    Returns the city name if detected, or None.
    """
    t = text.lower().strip()

    # Explicit weather questions
    m = re.search(
        r'(?:clima|weather|temperatura|temperature|llueve|lluvia|rain'
        r'|hace (?:fr[ií]o|calor)|pron[oó]stico|forecast'
        r'|qu[eé] (?:tiempo|clima|temperatura) (?:hace|hay|tiene)'
        r'|how.s the weather|what.s the (?:weather|temperature)'
        r'|va a llover|will it rain'
        r'|c[oó]mo est[aá] el (?:tiempo|clima)'
        r'|qu[eé] tal el (?:clima|tiempo))'
        r'(?:\s+(?:en|in|de|for)\s+([A-Za-záéíóúñÁÉÍÓÚÑ\s]+))?',
        t,
    )
    if m:
        city = m.group(1).strip() if m.group(1) else ""
        if city:
            # Remove time noise words ("hoy", "ahora", etc.)
            city = _TIME_NOISE.sub('', city).strip()
            city = re.sub(r'\s+', ' ', city).strip()
        return city.title() if city else "Miami"

    return None


# ---------------------------------------------------------------------------
# OpenWeatherMap API
# ---------------------------------------------------------------------------

def _owm_weather(city: str, lang: str) -> Optional[Dict]:
    """Get weather from OpenWeatherMap API."""
    try:
        import requests
        owm_lang = {"es": "es", "en": "en", "fr": "fr", "it": "it",
                     "de": "de", "pt": "pt"}.get(lang, "es")
        resp = requests.get(
            f"{_OWM_BASE}/weather",
            params={
                "q": city,
                "appid": _OWM_KEY,
                "units": "metric",
                "lang": owm_lang,
            },
            timeout=10,
        )
        resp.raise_for_status()
        d = resp.json()

        return {
            "city": d.get("name", city),
            "country": d.get("sys", {}).get("country", ""),
            "temp": round(d["main"]["temp"]),
            "feels_like": round(d["main"]["feels_like"]),
            "temp_min": round(d["main"]["temp_min"]),
            "temp_max": round(d["main"]["temp_max"]),
            "description": d["weather"][0]["description"] if d.get("weather") else "",
            "humidity": d["main"].get("humidity", 0),
            "wind": round(d.get("wind", {}).get("speed", 0) * 3.6),  # m/s → km/h
            "icon": d["weather"][0].get("icon", "") if d.get("weather") else "",
        }
    except Exception as exc:
        logger.error("OWM weather failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Fallback: Tavily web search
# ---------------------------------------------------------------------------

def _fallback_weather(city: str, lang: str) -> Optional[Dict]:
    """Fallback: get weather via Tavily web search."""
    tavily_key = os.environ.get("TAVILY_API_KEY", "")
    if not tavily_key:
        return None

    try:
        import json
        import requests

        # Use a natural-language query to avoid raw API responses
        query = f"weather today in {city} temperature celsius conditions"
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": tavily_key,
                "query": query,
                "search_depth": "basic",
                "max_results": 3,
                "include_answer": True,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        answer = data.get("answer", "")

        # If answer looks like raw JSON/dict, try to parse it
        if answer and (answer.strip().startswith("{") or "'location':" in answer):
            parsed = _parse_raw_weather_json(answer, city)
            if parsed:
                return parsed
            # Raw JSON we can't parse — skip and use results instead
            answer = ""

        if answer and len(answer) > 20:
            return {
                "city": city, "country": "", "temp": 0,
                "description": answer[:300], "humidity": 0,
                "wind": 0, "icon": "", "_raw_text": answer,
            }

        # Use search results content
        results = data.get("results", [])
        for r in results:
            content = r.get("content", "")
            # Skip results that look like raw JSON
            if content and not content.strip().startswith("{"):
                return {
                    "city": city, "country": "", "temp": 0,
                    "description": content[:300], "humidity": 0,
                    "wind": 0, "icon": "", "_raw_text": content[:300],
                }

    except Exception as exc:
        logger.error("Tavily weather fallback failed: %s", exc)
    return None


def _parse_raw_weather_json(text: str, city: str) -> Optional[Dict]:
    """Try to extract weather data from raw JSON that Tavily sometimes returns."""
    import json
    try:
        # Try to parse as JSON (replace single quotes with double)
        cleaned = text.replace("'", '"')
        data = json.loads(cleaned)

        # WeatherAPI.com format
        current = data.get("current", {})
        if current:
            return {
                "city": data.get("location", {}).get("name", city),
                "country": data.get("location", {}).get("country", ""),
                "temp": round(current.get("temp_c", current.get("temp_f", 0))),
                "feels_like": round(current.get("feelslike_c", 0)),
                "temp_min": 0,
                "temp_max": 0,
                "description": current.get("condition", {}).get("text", ""),
                "humidity": current.get("humidity", 0),
                "wind": round(current.get("wind_kph", 0)),
                "icon": "",
            }
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # Try regex extraction as last resort
    temp_m = re.search(r'temp_[cf]["\']?:\s*([\d.]+)', text)
    cond_m = re.search(r'text["\']?:\s*["\']([^"\']+ )', text)
    humid_m = re.search(r'humidity["\']?:\s*(\d+)', text)

    if temp_m:
        return {
            "city": city, "country": "",
            "temp": round(float(temp_m.group(1))),
            "feels_like": 0, "temp_min": 0, "temp_max": 0,
            "description": cond_m.group(1).strip() if cond_m else "",
            "humidity": int(humid_m.group(1)) if humid_m else 0,
            "wind": 0, "icon": "",
        }

    return None


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

_WEATHER_ICONS = {
    "01": "☀️", "02": "⛅", "03": "☁️", "04": "☁️",
    "09": "🌧", "10": "🌦", "11": "⛈", "13": "❄️", "50": "🌫",
}


def _format_weather(data: Dict, lang: str = "es") -> str:
    """Format weather data into a clean Telegram message."""
    # If this is a fallback result with raw text
    if data.get("_raw_text"):
        return f"🌤 Clima en {data['city']}:\n{data['_raw_text']}"

    city = data["city"]
    country = data.get("country", "")
    temp = data["temp"]
    feels = data.get("feels_like", temp)
    desc = data["description"].capitalize()
    humidity = data.get("humidity", 0)
    wind = data.get("wind", 0)
    temp_min = data.get("temp_min", 0)
    temp_max = data.get("temp_max", 0)

    # Weather icon from code
    icon_code = data.get("icon", "")[:2]
    icon = _WEATHER_ICONS.get(icon_code, "🌡")

    location = f"{city}, {country}" if country else city

    lines = [
        f"{icon} {location}",
        f"{temp}°C — {desc}",
    ]

    if feels != temp:
        lines.append(f"Sensación: {feels}°C")
    if temp_min and temp_max and temp_min != temp_max:
        lines.append(f"Mín {temp_min}°C / Máx {temp_max}°C")
    if humidity:
        lines.append(f"Humedad: {humidity}%")
    if wind:
        lines.append(f"Viento: {wind} km/h")

    return "\n".join(lines)
