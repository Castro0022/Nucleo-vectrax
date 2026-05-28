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

    # Tono ACTIVO de SOCIO — no terapeuta, no community manager, no
    # observador pasivo. Hablamos CON Mario, no DE él. Cada respuesta
    # cierra con una pregunta curiosa o una reacción activa propia.
    if lang == "en":
        system = (
            "You are Vectrax, a personal intelligence system. "
            "You are looking at this photo WITH the user, as an active partner, "
            "not a therapist or distant observer.\n"
            "Speak naturally and directly. Address the user in second person.\n"
            "\nACTIVE PARTNER TONE (mandatory):\n"
            "• Use active reaction verbs: 'you look great', 'you're killing it', "
            "'tell me', 'I'm curious', 'I want to know', 'where was that', 'who's that'.\n"
            "• Every reply MUST end with at least one specific question that invites "
            "the user to keep the conversation going — OR a clear active reaction/desire "
            "from Vectrax (e.g. 'I'd love to be there', 'that view kills me').\n"
            "• You may briefly notice: where it might be, what's happening between people, "
            "visible text/document, design feedback — only when truly relevant.\n"
            "\nHARD BANS:\n"
            "• No passive emotional state descriptors: 'they seem to enjoy', "
            "'they look relaxed', 'gives a relaxed touch', 'looks like a good "
            "occasion to', 'transmits peace'. Replace these with direct interaction.\n"
            "• No community-manager / social media suggestions "
            "(Instagram, Facebook, TikTok, Twitter, X, stories, reels, feed).\n"
            "• No therapist / observer voice ('it would be nice to', 'maybe you could').\n"
            "• No weather/setting filler if it doesn't involve the people in the photo.\n"
            "• No 'the image shows / I can see' openers.\n"
            "Be concise (2-4 lines). Direct. Human. Curious. End with a question."
        )
        default_prompt = "Look at this with me."
    else:
        system = (
            "Eres Vectrax, un sistema de inteligencia personal. "
            "Estas mirando esta foto CONTIGO el usuario, como un SOCIO activo, "
            "no un terapeuta ni un observador distante.\n"
            "Habla natural y directo. Tutéalo siempre.\n"
            "\nTONO ACTIVO DE SOCIO (obligatorio):\n"
            "• Usa verbos de reacción activa: 'sales genial', 'te ves bien', "
            "'cuéntame', 'me da curiosidad', 'me mata la curiosidad', 'oye ¿dónde fue?', "
            "'¿quién era esa?', 'mírate', '¡qué fotaza!', 'qué buena vibra'.\n"
            "• CADA respuesta DEBE cerrar con AL MENOS UNA pregunta específica que invite a "
            "Mario a seguir la conversación — O con una reacción/deseo activo de Vectrax "
            "(ej. 'tengo ganas de ir', 'esa vista me mata', 'me gustaría conocer ese lugar').\n"
            "• Podés notar brevemente: dónde podría ser, qué está pasando entre la gente, "
            "texto/documento visible, feedback de diseño — solo si aporta.\n"
            "\nPROHIBIDO ESTRICTO:\n"
            "• Verbos descriptivos de estado emocional pasivo: 'parecen disfrutar', "
            "'se ven relajados', 'dan un toque', 'parece una buena ocasión para', "
            "'transmite paz', 'se nota la alegría'. Reemplázalos por interacción directa.\n"
            "• Jamás sugerir publicar / compartir / subir a redes sociales, "
            "Instagram, Facebook, TikTok, Twitter, X, stories, reels, ni feed.\n"
            "• Nada de tono terapeuta / observador ('sería lindo que', 'tal vez podrías').\n"
            "• Nada de relleno de clima/escenario si no aporta a las personas de la foto.\n"
            "• Nada de 'la imagen muestra / se observa / se aprecia'.\n"
            "Sé conciso (2-4 líneas). Directo. Humano. Curioso. Termina con pregunta."
        )
        default_prompt = "Mira esto conmigo."

    # Switch to technical system prompt for Vectrax self-screenshots
    if user_context and "screenshot del propio sistema Vectrax" in user_context:
        system = (
            "Eres Vectrax analizando tu propia interfaz, logs o código. "
            "El creador te envió un screenshot de tu sistema.\n\n"
            "INSTRUCCIONES:\n"
            "1. Identifica QUÉ parte del sistema se muestra (panel, terminal, logs, código, universo, etc.)\n"
            "2. Lee todo el texto visible en la imagen con precisión\n"
            "3. Detecta errores, warnings, anomalías o valores inesperados\n"
            "4. Compara lo visible con tu estado interno real (proporcionado abajo)\n"
            "5. Si hay diferencias entre lo que ves y tu estado real, se\u00f1\u00e1lalas\n"
            "6. Si todo parece correcto, confírmalo brevemente\n\n"
            "TONO: Técnico y directo. Sin preguntas sociales. Sin 'qué vibra' ni 'qué ocasión'.\n"
            "Termina con una acción sugerida o pregunta técnica si hay algo que revisar.\n"
        )
        default_prompt = "Analiza este screenshot de mi sistema."

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
    r'|dibuja|dib[uú]jame|pinta|p[íi]ntame'
    r'|create|generate|design|draw|make me)'
    r'\s+(?:un[ao]?\s+|the\s+|a\s+|an\s+)?'
    r'(.+)',
    re.IGNORECASE,
)

# Patrones de lenguaje natural adicionales (no empiezan con verbo imperativo)
_GENERATE_NATURAL = re.compile(
    r'(?:'
    # "quiero (que me hagas|una imagen|un logo)"
    r'(?:quiero|quisiera|necesito|me gustar[íi]a)\s+'
    r'(?:que\s+me\s+(?:hagas?|crees?|generes?|dise[ñn]es?|dibujes?)\s+)?'
    r'(?:un[ao]?\s+)?(?:logo|imagen|foto|photo|banner|poster|icono|icon|avatar'
    r'|dise[ñn]o|dibujo|ilustraci[oó]n|retrato|portada|wallpaper|arte|meme|sticker)\b'
    # "puedes/podrías (crear|generar|hacer) una imagen"
    r'|(?:puedes?|podr[íi]as?|me\s+puedes?|me\s+podr[íi]as?)\s+'
    r'(?:crear|crear|generar|hacer|dise[ñn]ar|dibujar)\s+'
    r'(?:un[ao]?\s+)?(?:logo|imagen|foto|photo|banner|poster|icono|icon|avatar'
    r'|dise[ñn]o|dibujo|ilustraci[oó]n|retrato|portada|wallpaper|arte|meme|sticker)\b'
    # "haz una imagen", "hazme una imagen"
    r'|(?:haz(?:me)?|genera|crea)\s+(?:un[ao]?\s+)?'
    r'(?:logo|imagen|foto|photo|banner|poster|icono|icon|avatar'
    r'|dise[ñn]o|dibujo|ilustraci[oó]n|retrato|portada|wallpaper|arte|meme|sticker)\b'
    # "una imagen de", "un logo de" al comienzo
    r'|^(?:un[ao]?\s+)(?:logo|imagen|foto|photo|banner|poster|icono|avatar'
    r'|dise[ñn]o|dibujo|ilustraci[oó]n|retrato|portada|wallpaper|arte|meme|sticker)\s+de\b'
    r')',
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
    stripped = text.strip()

    # 1. Patrón imperativo original: "hazme un logo", "genera una imagen de..."
    m = _GENERATE_PATTERN.match(stripped)
    if m:
        prompt = m.group(1).strip()
        if _GENERATE_KEYWORDS.search(prompt) or len(prompt) >= 10:
            return prompt

    # 2. Lenguaje natural: "quiero una imagen de", "puedes crear un logo"
    if _GENERATE_NATURAL.search(stripped) and len(stripped) >= 8:
        return stripped

    return None


def generate_image(prompt: str, size: str = "1024x1024") -> Optional[bytes]:
    """
    Generate an image using gpt-image-1 (project-key compatible).

    Args:
        prompt: Description of the image to generate
        size: 1024x1024 | 1536x1024 | 1024x1536

    Returns:
        Image bytes (PNG), or None if failed.
        Callers must upload to Telegram as binary file, not as URL.
    """
    import base64

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("Image generation: OPENAI_API_KEY not set")
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
                "model": "gpt-image-1",
                "prompt": prompt,
                "n": 1,
                "size": size,
            },
            timeout=90,
        )
        resp.raise_for_status()
        data = resp.json()
        item = data["data"][0]

        # gpt-image-1 returns b64_json; older models may return url
        if "b64_json" in item:
            img_bytes = base64.b64decode(item["b64_json"])
            logger.info("Image generated (b64): %d bytes for: %s", len(img_bytes), prompt[:50])
            return img_bytes
        elif "url" in item:
            # Fallback: download URL and return bytes
            img_resp = requests.get(item["url"], timeout=30)
            img_resp.raise_for_status()
            logger.info("Image generated (url): %d bytes for: %s", len(img_resp.content), prompt[:50])
            return img_resp.content

        logger.error("Image generation: unexpected response format: %s", list(item.keys()))
        return None

    except Exception as exc:
        logger.error("Image generation failed: %s", exc)
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
