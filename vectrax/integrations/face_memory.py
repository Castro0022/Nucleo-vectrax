"""
Vectrax Face Memory — Reconocimiento de personas
====================================================
Registra caras y las reconoce después usando GPT-4o vision.

Flujo:
  1. Usuario envía foto + "Este soy yo" → guarda cara + genera descripción física
  2. Usuario envía foto + "Esta es Ana" → guarda cara de Ana
  3. Cualquier foto posterior → compara con caras registradas → identifica

Almacenamiento:
  - SQLite: face_registry (user_id, person_name, description, photo_b64)
  - La descripción física permite reconocimiento rápido sin enviar todas las fotos

Creado: 2026-04-18
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import base64
import logging
import os
import re
import sqlite3
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.face_memory")

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "vault", "face_registry.db",
)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS face_registry (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    person_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    photo_b64   TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_face_user ON face_registry(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_face_unique ON face_registry(user_id, person_name);
"""


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.executescript(_CREATE_SQL)
    return conn


# ---------------------------------------------------------------------------
# Registration detection
# ---------------------------------------------------------------------------

_REGISTER_SELF = re.compile(
    r'(?:est[ea]\s+soy\s+yo|soy\s+yo|this\s+is\s+me|that.s\s+me'
    r'|mi\s+(?:foto|cara|rostro|face)|my\s+(?:photo|face)'
    r'|as[ií]\s+me\s+veo|recuerda\s+(?:mi\s+)?(?:cara|rostro))',
    re.IGNORECASE,
)

_REGISTER_OTHER = re.compile(
    r'(?:est[ea]\s+es|this\s+is|[eé]l\s+es|ella\s+es|he\s+is|she\s+is'
    r'|se\s+llama|su\s+nombre\s+es|(?:his|her)\s+name\s+is)\s+'
    r'([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?)',
    re.IGNORECASE | re.UNICODE,
)


def detect_registration(caption: str) -> Optional[Tuple[str, str]]:
    """
    Detect if a photo caption is registering a face.

    Returns:
        ("self", "") for self-registration
        ("other", "Name") for registering another person
        None if not a registration
    """
    if not caption:
        return None

    if _REGISTER_SELF.search(caption):
        return ("self", "")

    m = _REGISTER_OTHER.search(caption)
    if m:
        name = m.group(1).strip()
        return ("other", name)

    return None


# ---------------------------------------------------------------------------
# Face registration
# ---------------------------------------------------------------------------

def register_face(
    user_id: str,
    person_name: str,
    image_data: bytes,
) -> Optional[Dict]:
    """
    Register a face: store photo + generate physical description via GPT-4o.

    Args:
        user_id: Owner user ID
        person_name: Name of the person in the photo
        image_data: Raw image bytes

    Returns:
        Dict with registration info, or None if failed.
    """
    import time

    # Encode photo as base64 (compressed — store only what we need)
    photo_b64 = base64.b64encode(image_data).decode("ascii")

    # Generate physical description using GPT-4o
    description = _describe_face(photo_b64, person_name)
    if not description:
        description = f"Persona registrada como {person_name}"

    # Store in DB (upsert)
    try:
        db = _conn()
        db.execute(
            """INSERT INTO face_registry (user_id, person_name, description, photo_b64, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, person_name)
               DO UPDATE SET description=?, photo_b64=?, created_at=?""",
            (user_id, person_name, description, photo_b64, time.time(),
             description, photo_b64, time.time()),
        )
        db.commit()
        db.close()

        logger.info("FACE registered | user=%s | name=%s", user_id[:15], person_name)
        return {
            "person_name": person_name,
            "description": description,
        }
    except Exception as exc:
        logger.error("register_face failed: %s", exc)
        return None


def _describe_face(photo_b64: str, person_name: str) -> str:
    """Generate a physical description of the person using GPT-4o vision."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return ""

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
                    {
                        "role": "system",
                        "content": (
                            "Describe the physical appearance of the main person in this photo "
                            "in a way that would help identify them later. Include: approximate age, "
                            "gender, hair color/style, skin tone, facial hair, distinctive features, "
                            "build. Be factual and concise (2-3 lines). "
                            "Format: 'Person_name: description'"
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": f"Describe this person (named {person_name}):"},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{photo_b64}",
                                    "detail": "low",
                                },
                            },
                        ],
                    },
                ],
                "max_tokens": 200,
                "temperature": 0.2,
            },
            timeout=25,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.error("_describe_face failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Face recognition
# ---------------------------------------------------------------------------

def get_registered_faces(user_id: str, include_photo: bool = False) -> List[Dict]:
    """Get all registered faces for a user."""
    try:
        db = _conn()
        cols = "person_name, description, photo_b64" if include_photo else "person_name, description"
        rows = db.execute(
            f"SELECT {cols} FROM face_registry WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        db.close()
        results = []
        for r in rows:
            d = {"name": r["person_name"], "description": r["description"]}
            if include_photo:
                d["photo_b64"] = r["photo_b64"]
            results.append(d)
        return results
    except Exception:
        return []


def recognize_faces(
    user_id: str,
    image_url: str,
) -> Optional[List[str]]:
    """
    Try to recognize people in a photo by comparing against registered faces.

    Sends reference photos (base64) alongside the new photo for direct
    visual comparison — much more accurate than description-only matching.

    Returns list of recognized names, or None if no faces registered.
    """
    faces = get_registered_faces(user_id, include_photo=True)
    if not faces:
        return None

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return None

    # Build content array: reference photos + the new photo
    # Limit to 5 reference faces to stay within API limits
    content_parts = []

    # Add reference photos with labels
    ref_labels = []
    for f in faces[:5]:
        if f.get("photo_b64"):
            ref_labels.append(f["name"])
            content_parts.append({
                "type": "text",
                "text": f"Reference photo of {f['name']}:",
            })
            content_parts.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{f['photo_b64']}",
                    "detail": "low",
                },
            })

    if not ref_labels:
        return None

    # Add the new photo to analyze
    content_parts.append({
        "type": "text",
        "text": "New photo to analyze — who from the reference photos appears here?",
    })
    content_parts.append({
        "type": "image_url",
        "image_url": {"url": image_url, "detail": "low"},
    })

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
                    {
                        "role": "system",
                        "content": (
                            "You are comparing reference photos of known people against a new photo. "
                            f"Known people: {', '.join(ref_labels)}.\n"
                            "Determine if any of these people appear in the new photo. "
                            "Compare facial features carefully: face shape, eyes, nose, skin tone, hair.\n"
                            "Respond with ONLY the names of people you recognize, comma-separated. "
                            "If nobody matches, respond with: NONE"
                        ),
                    },
                    {
                        "role": "user",
                        "content": content_parts,
                    },
                ],
                "max_tokens": 100,
                "temperature": 0.2,
            },
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()["choices"][0]["message"]["content"].strip()

        if result.upper() == "NONE" or not result:
            return []

        # Parse comma-separated names
        names = [n.strip() for n in result.split(",") if n.strip()]
        # Filter to only known names
        known_names = {f["name"].lower() for f in faces}
        recognized = [n for n in names if n.lower() in known_names]

        if recognized:
            logger.info("FACE recognized | user=%s | found=%s", user_id[:15], recognized)

        return recognized

    except Exception as exc:
        logger.error("recognize_faces failed: %s", exc)
        return None


def download_photo(token: str, file_id: str) -> Optional[bytes]:
    """Download a photo from Telegram and return raw bytes."""
    try:
        import requests
        # Get file path
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": file_id},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            return None
        file_path = data["result"]["file_path"]

        # Download file
        dl = requests.get(
            f"https://api.telegram.org/file/bot{token}/{file_path}",
            timeout=15,
        )
        dl.raise_for_status()
        return dl.content
    except Exception as exc:
        logger.error("download_photo failed: %s", exc)
        return None
