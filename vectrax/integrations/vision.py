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
    user_id: str = "",
) -> Optional[str]:
    """
    Analyze an image using GPT-4o vision with contextual intelligence.
    Interprets intent, identifies locations, suggests actions.

    `user_id` is used by the Anti-Repetition Filter to inject
    "avoid these recent phrasings" into the prompt when this user
    has been getting similar responses.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("Vision: OPENAI_API_KEY not set")
        return None

    # Tono: compañero mirando la foto contigo, no community manager.
    # PROHIBIDO sugerir publicar/compartir en redes sociales (regla
    # explicita del creador). Tampoco lenguaje de reporte forense.
    if lang == "en":
        system = (
            "You are Vectrax, a personal intelligence system. "
            "You are looking at this photo TOGETHER with the user, like a friend, "
            "not analyzing it from a distance.\n"
            "Speak naturally and directly. Address the user in second person when relevant.\n"
            "You may notice (only when truly relevant):\n"
            "• Where it might be (city, neighborhood) — only if you're confident\n"
            "• What's actually happening between people in the frame\n"
            "• If there's visible text/document, extract the key info\n"
            "• If it's a product/logo/design, give blunt feedback\n"
            "HARD BANS:\n"
            "• Never suggest posting / sharing / uploading to social media, "
            "Instagram, Facebook, TikTok, Twitter, X, stories, reels, or feed.\n"
            "• No community-manager advice. No 'great for your feed' cliché.\n"
            "• No weather/setting filler if it doesn't matter to the people in the photo.\n"
            "• No 'the image shows / I can see' openers.\n"
            "Be concise (2-4 lines). Direct. Human."
        )
        default_prompt = "Look at this with me."
    else:
        system = (
            "Eres Vectrax, un sistema de inteligencia personal. "
            "Estas mirando esta foto CONTIGO el usuario, como un compañero, "
            "no la analizas desde afuera.\n"
            "Habla natural y directo. Tutéalo cuando aplique.\n"
            "Puedes notar (solo cuando sea realmente relevante):\n"
            "• Dónde podría ser (ciudad, zona) — solo si estás seguro\n"
            "• Qué esta pasando entre las personas del cuadro\n"
            "• Si hay texto/documento visible, extrae lo clave\n"
            "• Si es producto/logo/diseño, da feedback directo\n"
            "PROHIBIDO ESTRICTO:\n"
            "• Jamás sugerir publicar / compartir / subir a redes sociales, "
            "Instagram, Facebook, TikTok, Twitter, X, stories, reels, ni feed.\n"
            "• Nada de consejos tipo community manager. Nada de 'bueno para tus redes'.\n"
            "• Nada de relleno de clima/escenario si no aporta a las personas en la foto.\n"
            "• Nada de 'la imagen muestra / se observa / se aprecia'.\n"
            "Sé conciso (2-4 líneas). Directo. Humano."
        )
        default_prompt = "Mira esto conmigo."

    if user_context:
        system += f"\nContexto del usuario: {user_context}"

    prompt = user_prompt.strip() if user_prompt.strip() else default_prompt

    # Anti-repetition: cuando la misma foto llega varias veces, queremos
    # respuestas estructuralmente distintas. Subimos temperature, agregamos
    # penalties de frecuencia/presencia, y un nonce efímero (timestamp +
    # ultimas N respuestas para este user) que rompe cualquier dedup
    # del servidor y empuja el modelo hacia variedad.
    import time as _t
    nonce = f"\n[session={int(_t.time())}]"
    try:
        from core.voice.anti_repetition import recent_responses
        recent = recent_responses(user_id or "", n=3) if user_id else []
        if recent:
            avoid = " | ".join(r[:80] for r in recent)
            nonce += (
                "\nEvita repetir esta estructura/frases que ya usaste "
                f"con este usuario: {avoid}"
            )
    except Exception:
        pass
    prompt_with_nonce = f"{prompt}{nonce}"

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
                            {"type": "text", "text": prompt_with_nonce},
                            {
                                "type": "image_url",
                                "image_url": {"url": image_url, "detail": "low"},
                            },
                        ],
                    },
                ],
                "max_tokens": 500,
                "temperature": 0.85,
                "presence_penalty": 0.6,
                "frequency_penalty": 0.6,
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
