"""
Vectrax Preference Tracker — Preferencias Reveladas de Contactos
=================================================================
Aprende cómo tratar a cada contacto basándose en lo que el usuario
revela en conversación natural.

Regla de activación:
  señal barata (regex) + persona identificable + contexto comercial/relacional
  → LLM extrae preferencia estructurada → guardado limpio

Solo categorías con valor comercial:
  comunicación    — cómo prefiere hablar, qué formato
  decisión        — cómo toma decisiones, qué necesita para decidir
  objeciones      — qué rechaza, qué le molesta
  presupuesto     — sensibilidad al precio, rangos
  urgencia        — cómo maneja los tiempos
  formato         — reportes cortos, detallados, visuales
  aversión        — qué no soporta explícitamente

Activación:
  Solo cuando hay contexto de ese contacto: follow-up, propuesta,
  preparación de reunión. No en conversaciones genéricas.

Creado: 2026-04-02
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.preference_tracker")

_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "leads.db",
)

_CREATE = """
CREATE TABLE IF NOT EXISTS contact_preferences (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    contact     TEXT NOT NULL,
    category    TEXT NOT NULL,
    preference  TEXT NOT NULL,
    polarity    TEXT NOT NULL DEFAULT 'positive',
    confidence  REAL NOT NULL DEFAULT 0.8,
    raw_input   TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pref_user    ON contact_preferences(user_id);
CREATE INDEX IF NOT EXISTS idx_pref_contact ON contact_preferences(user_id, contact);
"""

# ---------------------------------------------------------------------------
# Señales baratas (regex) — nivel 1
# ---------------------------------------------------------------------------

_PREFERENCE_SIGNALS = re.compile(
    r"\b(?:me\s+gusta|prefiero|prefiere|le\s+gusta|adora|disfruta|\
me\s+encanta|le\s+encanta|favorito|favorita|\
odio|odia|detesto|detesta|no\s+me\s+gusta|no\s+le\s+gusta|\
no\s+soporto|no\s+soporta|le\s+molesta|me\s+molesta|\
le\s+cuesta|no\s+le\s+importa|prefiere\s+que|\
likes?|loves?|hates?|prefers?|can\'?t\s+stand|doesn\'?t\s+like)\b",
    re.IGNORECASE,
)

# Categorías comerciales válidas
_COMMERCIAL_CATEGORIES = {
    "comunicación": ["mensaje", "email", "llamada", "directo", "corto", "largo",
                     "format", "whatsapp", "telegram", "responde", "contesta"],
    "decisión":     ["decide", "piensa", "analiza", "necesita ver", "convence",
                     "tiempo para decidir", "impulsivo", "cuidadoso"],
    "objeciones":   ["precio", "caro", "barato", "tiempo", "urgente", "riesgo",
                     "garantía", "prueba", "no confía", "desconfía"],
    "presupuesto":  ["presupuesto", "budget", "pagar", "invertir", "costo",
                     "tarifa", "precio", "económico", "premium"],
    "urgencia":     ["urgente", "rápido", "espera", "paciencia", "plazo",
                     "deadline", "entrega", "mañana", "hoy"],
    "formato":      ["reporte", "resumen", "detalle", "visual", "números",
                     "excel", "powerpoint", "corto", "largo", "simple"],
    "aversión":     ["no soporta", "odia", "detesta", "nunca", "jamás",
                     "no quiere", "evita", "rechaza"],
}


def _has_commercial_context(text: str) -> bool:
    """True si el texto tiene contexto comercial/relacional."""
    for kws in _COMMERCIAL_CATEGORIES.values():
        for kw in kws:
            if kw.lower() in text.lower():
                return True
    return False


def _extract_person_name(text: str) -> Optional[str]:
    """Extrae un nombre propio del texto (nombre de contacto mencionado)."""
    # Buscar nombre en mayúscula cerca de la señal de preferencia
    m = re.search(
        r"\b([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?)"
        r"(?:\s+(?:prefiere|le\s+gusta|odia|no\s+soporta|detesta))"
        r"|(?:(?:prefiere|le\s+gusta|odia|no\s+soporta|detesta)\s+(?:que\s+)?\w+\s+)"
        r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)",
        text,
        re.UNICODE,
    )
    if m:
        return (m.group(1) or m.group(2) or "").strip()
    return None


# ---------------------------------------------------------------------------
# Extracción LLM — nivel 2
# ---------------------------------------------------------------------------

_EXTRACT_PROMPT = (
    "Analiza este mensaje y extrae preferencias comerciales de un contacto específico.\n"
    "Solo extrae si hay: una preferencia clara + un nombre de persona + categoría comercial relevante.\n\n"
    "Categorías válidas: comunicación, decisión, objeciones, presupuesto, urgencia, formato, aversión\n\n"
    'Retorna JSON array (o [] si no aplica):\n'
    '[{{"contacto": "nombre", "categoria": "una válida", '
    '"preferencia": "concisa", "polaridad": "positiva|negativa", "confianza": 0.0}}]\n\n'
    "NO extraer preferencias del usuario sobre sí mismo, gustos triviales ni suposiciones.\n\n"
    'Mensaje: "{text}"\nJSON:'
)


def _extract_with_llm(text: str) -> List[Dict[str, Any]]:
    """Extrae preferencias estructuradas via LLM."""
    prompt = _EXTRACT_PROMPT.format(text=text[:400])
    try:
        import os, json, httpx
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return []
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 250,
                "temperature": 0.0,
            },
            timeout=6.0,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = raw.strip("`").lstrip("json").strip()
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except Exception as exc:
        logger.debug("Preference LLM extraction failed: %s", exc)
    return []


# ---------------------------------------------------------------------------
# Almacenamiento
# ---------------------------------------------------------------------------

def _db_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB), exist_ok=True)
    conn = sqlite3.connect(_DB, timeout=3)
    conn.executescript(_CREATE)
    return conn


def store_preference(
    user_id: str,
    contact: str,
    category: str,
    preference: str,
    polarity: str = "positive",
    confidence: float = 0.8,
    raw_input: str = "",
) -> bool:
    """Guarda o actualiza una preferencia de contacto."""
    now = time.time()
    conn = _db_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM contact_preferences "
            "WHERE user_id=? AND LOWER(contact)=LOWER(?) AND category=?",
            (user_id, contact, category),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE contact_preferences SET preference=?, polarity=?, "
                "confidence=?, updated_at=? WHERE id=?",
                (preference, polarity, confidence, now, existing[0]),
            )
        else:
            conn.execute(
                "INSERT INTO contact_preferences "
                "(user_id, contact, category, preference, polarity, confidence, raw_input, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (user_id, contact, category, preference, polarity, confidence, raw_input[:200], now, now),
            )
        conn.commit()
        logger.info(
            "Preference stored | user=%s | contact=%s | cat=%s | pol=%s",
            user_id[:20], contact, category, polarity,
        )
        return True
    except Exception as exc:
        logger.error("store_preference failed: %s", exc)
        return False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Detección principal
# ---------------------------------------------------------------------------

def detect_and_store(user_id: str, text: str) -> int:
    """
    Pipeline completo: señal → validación → LLM → almacenamiento.

    Retorna número de preferencias guardadas (0 si no aplica).
    """
    # Nivel 1: señal barata
    if not _PREFERENCE_SIGNALS.search(text):
        return 0

    # Verificar contexto comercial
    if not _has_commercial_context(text):
        return 0

    # Nivel 2: LLM extrae preferencias estructuradas
    items = _extract_with_llm(text)
    if not items:
        return 0

    stored = 0
    for item in items:
        contact = str(item.get("contacto", "")).strip()
        category = str(item.get("categoria", "")).strip().lower()
        preference = str(item.get("preferencia", "")).strip()
        polarity = str(item.get("polaridad", "positiva")).strip().lower()
        confidence = float(item.get("confianza", 0.8))

        # Validaciones
        if not contact or not preference or len(preference) < 5:
            continue
        if category not in _COMMERCIAL_CATEGORIES:
            continue
        if confidence < 0.6:  # descartar extracciones poco confiables
            continue

        pol = "positive" if "positiv" in polarity or "positiv" in polarity else "negative"
        if store_preference(user_id, contact, category, preference, pol, confidence, text):
            stored += 1

    return stored


# ---------------------------------------------------------------------------
# Consulta — activación contextual
# ---------------------------------------------------------------------------

def get_contact_preferences(
    user_id: str,
    contact: str,
) -> List[Dict[str, Any]]:
    """
    Retorna las preferencias conocidas de un contacto.
    Solo categorías de alta confianza (>= 0.65).
    """
    conn = _db_conn()
    try:
        rows = conn.execute(
            "SELECT category, preference, polarity, confidence FROM contact_preferences "
            "WHERE user_id=? AND LOWER(contact)=LOWER(?) AND confidence >= 0.65 "
            "ORDER BY confidence DESC, updated_at DESC",
            (user_id, contact),
        ).fetchall()
        return [
            {"category": r[0], "preference": r[1], "polarity": r[2], "confidence": r[3]}
            for r in rows
        ]
    finally:
        conn.close()


def build_contact_context(user_id: str, contact: str) -> str:
    """
    Construye el contexto de preferencias para inyectar antes de un follow-up.
    Solo si hay preferencias relevantes guardadas.

    Retorna string como:
      'Recuerda sobre Carlos: prefiere mensajes directos (comunicación),
       sensible al precio (presupuesto).'
    """
    prefs = get_contact_preferences(user_id, contact)
    if not prefs:
        return ""

    parts = []
    for p in prefs[:4]:  # máx 4 para no sobrecargar el prompt
        pol_note = "" if p["polarity"] == "positive" else " [evitar]"
        parts.append(f"{p['preference']}{pol_note} ({p['category']})")

    return f"Sobre {contact}: {', '.join(parts)}."
