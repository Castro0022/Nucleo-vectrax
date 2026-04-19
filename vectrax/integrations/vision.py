"""
Vectrax Vision — Análisis + Generación de imágenes
====================================================
Dos capacidades:

1. ANÁLISIS (GPT-4o vision):
   Usuario envía foto → Vectrax interpreta con contexto:
   - Identifica ubicaciones (Brickell, Biscayne Bay)
   - Interpreta intención (bueno para Instagram, alto valor inmobiliario)
   - Sugiere acciones concretas
   - Lee texto/documentos (OCR implícito)

2. GENERACIÓN (DALL-E 3):
   "Hazme un logo de Vectrax" → genera imagen y la envía como foto

Creado: 2026-04-18
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

logger = logging.getLogger("vectrax.vision")


# ---------------------------------------------------------------------------
# Image Analysis (GPT-4o vision)
# ---------------------------------------------------------------------------

def analyze_image(
    image_url: str,
    user_prompt: str = "",
    lang: str = "es",
    user_context: str = "",
) -> Optional[str]:
    """
    Analyze an image using GPT-4o vision with contextual intelligence.
    Interprets intent, identifies locations, suggests actions.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("Vision: OPENAI_API_KEY not set")
        return None

    if lang == "en":
        system = (
            "You are Vectrax, a personal intelligence system. "
            "Analyze the image with depth and intent — don't just describe, INTERPRET.\n"
            "Your analysis should include (when relevant):\n"
            "• Location identification (city, neighborhood, landmark)\n"
            "• Contextual insight (weather, time of day, season)\n"
            "• Practical suggestion (good for Instagram, business opportunity, etc.)\n"
            "• If text/document is visible, extract the key information\n"
            "• If it's a product/logo/design, evaluate quality and suggest improvements\n"
            "Be concise (3-5 lines). Direct. No filler."
        )
        default_prompt = "Analyze this image."
    else:
        system = (
            "Eres Vectrax, un sistema de inteligencia personal. "
            "Analiza la imagen con profundidad e intención — no solo describas, INTERPRETA.\n"
            "Tu análisis debe incluir (cuando sea relevante):\n"
            "• Identificación de ubicación (ciudad, zona, punto de referencia)\n"
            "• Insight contextual (clima, hora del día, temporada)\n"
            "• Sugerencia práctica (bueno para redes, oportunidad de negocio, etc.)\n"
            "• Si hay texto/documento visible, extrae la información clave\n"
            "• Si es producto/logo/diseño, evalúa calidad y sugiere mejoras\n"
            "Sé conciso (3-5 líneas). Directo. Sin relleno."
        )
        default_prompt = "Analiza esta imagen."

    if user_context:
        system += f"\nContexto del usuario: {user_context}"

    prompt = user_prompt.strip() if user_prompt.strip() else default_prompt

    try:
        import requests
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": image_url, "detail": "low"},
                            },
                        ],
                    },
                ],
                "max_tokens": 500,
                "temperature": 0.4,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"].strip()
        logger.info("Vision: analyzed image (%d chars response)", len(text))
        return text

    except Exception as exc:
        logger.error("Vision failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Image Generation (DALL-E 3)
# ---------------------------------------------------------------------------

_GENERATE_PATTERN = re.compile(
    r'^(?:hazme|crea|cre[aá]me|genera|gener[aá]me|dise[ñn]a|dise[ñn]ame'
    r'|dibuja|dib[uú]jame|pinta|p[ií]ntame'
    r'|create|generate|design|draw|make me)'
    r'\s+(?:un[ao]?\s+|the\s+|a\s+|an\s+)?'
    r'(.+)',
    re.IGNORECASE,
)

# Keywords that strongly suggest image generation
_GENERATE_KEYWORDS = re.compile(
    r'\b(?:logo|avatar|imagen|image|foto|photo|banner|poster|icono|icon'
    r'|ilustraci[oó]n|illustration|wallpaper|fondo|portada|cover'
    r'|dise[ñn]o|design|art|arte|dibujo|drawing|meme|sticker'
    r'|retrato|portrait|paisaje|landscape|escena|scene)\b',
    re.IGNORECASE,
)


def detect_generation_intent(text: str) -> Optional[str]:
    """
    Detect if the user wants to generate an image.
    Returns the prompt for DALL-E, or None.
    """
    m = _GENERATE_PATTERN.match(text.strip())
    if m:
        prompt = m.group(1).strip()
        # Require a generation keyword or decent description length
        if _GENERATE_KEYWORDS.search(prompt) or len(prompt) >= 10:
            return prompt
    return None


def generate_image(prompt: str, size: str = "1024x1024") -> Optional[str]:
    """
    Generate an image using DALL-E 3.

    Args:
        prompt: Description of the image to generate
        size: Image size (1024x1024, 1024x1792, 1792x1024)

    Returns:
        URL of the generated image, or None if failed.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("DALL-E: OPENAI_API_KEY not set")
        return None

    try:
        import requests
        resp = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "dall-e-3",
                "prompt": prompt,
                "n": 1,
                "size": size,
                "quality": "standard",
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        url = data["data"][0]["url"]
        logger.info("DALL-E: generated image for: %s", prompt[:50])
        return url

    except Exception as exc:
        logger.error("DALL-E generation failed: %s", exc)
        return None


def get_telegram_photo_url(token: str, file_id: str) -> Optional[str]:
    """
    Get direct URL for a Telegram photo file_id.

    Args:
        token: Telegram bot token
        file_id: File ID from the photo message

    Returns:
        Direct download URL or None.
    """
    try:
        import requests
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": file_id},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok") and data.get("result", {}).get("file_path"):
            file_path = data["result"]["file_path"]
            return f"https://api.telegram.org/file/bot{token}/{file_path}"
    except Exception as exc:
        logger.error("get_telegram_photo_url failed: %s", exc)
    return None
