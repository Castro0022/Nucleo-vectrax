"""
Vectrax Lead Tracker — Seguimiento Proactivo de Leads
=======================================================
Detecta cuando un lead lleva N días sin respuesta y genera
automáticamente el mensaje de seguimiento correcto.

Flujo:
  1. Usuario registra lead: "Carlos es un lead para diseño de logo"
  2. Vectrax guarda el lead con estado 'contacted'
  3. Después de SILENCE_DAYS sin actividad → dispara sugerencia
  4. Mensaje llega: "Carlos lleva 3 días sin responder.
     Sugerencia: 'Hola Carlos, ¿tuviste oportunidad de revisar la propuesta?'"

Tabla lead_activities:
  id, user_id, lead_name, status, context, last_contact, created_at

Estados:
  contacted      — primer contacto realizado
  proposal_sent  — propuesta enviada, esperando respuesta
  negotiating    — en negociación activa
  silent         — sin respuesta por más de SILENCE_DAYS
  closed_won     — cerrado con éxito
  closed_lost    — perdido

Creado: 2026-04-02
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.lead_tracker")

_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "leads.db",
)

_CREATE = """
CREATE TABLE IF NOT EXISTS leads (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    name         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'contacted',
    context      TEXT NOT NULL DEFAULT '',
    last_contact REAL NOT NULL,
    created_at   REAL NOT NULL,
    notes        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_leads_user   ON leads(user_id);
CREATE INDEX IF NOT EXISTS idx_leads_name   ON leads(user_id, name);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(user_id, status);

CREATE TABLE IF NOT EXISTS lead_activities (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id    INTEGER NOT NULL,
    user_id    TEXT NOT NULL,
    activity   TEXT NOT NULL DEFAULT '',
    note       TEXT NOT NULL DEFAULT '',
    timestamp  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_la_lead ON lead_activities(lead_id);
"""

SILENCE_DAYS = 3       # días sin respuesta para disparar follow-up
ACTIVE_STATUSES = ("contacted", "proposal_sent", "negotiating")


# ---------------------------------------------------------------------------
# Conexión
# ---------------------------------------------------------------------------

def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB), exist_ok=True)
    conn = sqlite3.connect(_DB, timeout=3)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_CREATE)
    return conn


# ---------------------------------------------------------------------------
# CRUD de leads
# ---------------------------------------------------------------------------

def add_lead(
    user_id: str,
    name: str,
    context: str = "",
    status: str = "contacted",
) -> Dict[str, Any]:
    """Registra un nuevo lead o actualiza si ya existe."""
    now = time.time()
    conn = _conn()
    try:
        existing = conn.execute(
            "SELECT id FROM leads WHERE user_id=? AND LOWER(name)=LOWER(?)",
            (user_id, name),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE leads SET status=?, context=?, last_contact=?, notes=? WHERE id=?",
                (status, context or "", now, "", existing[0]),
            )
            conn.commit()
            return {"ok": True, "action": "updated", "name": name, "status": status}
        else:
            conn.execute(
                "INSERT INTO leads (user_id, name, status, context, last_contact, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (user_id, name, status, context, now, now),
            )
            conn.commit()
            logger.info("Lead added | user=%s | name=%s | status=%s", user_id[:20], name, status)
            return {"ok": True, "action": "created", "name": name, "status": status}
    finally:
        conn.close()


def update_lead_status(user_id: str, name: str, status: str, note: str = "") -> bool:
    """Actualiza el estado de un lead y registra la actividad."""
    now = time.time()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id FROM leads WHERE user_id=? AND LOWER(name)=LOWER(?)",
            (user_id, name),
        ).fetchone()
        if not row:
            return False
        lead_id = row[0]
        conn.execute(
            "UPDATE leads SET status=?, last_contact=? WHERE id=?",
            (status, now, lead_id),
        )
        if note:
            conn.execute(
                "INSERT INTO lead_activities (lead_id, user_id, activity, note, timestamp) "
                "VALUES (?,?,?,?,?)",
                (lead_id, user_id, status, note, now),
            )
        conn.commit()
        return True
    finally:
        conn.close()


def record_contact(user_id: str, name: str, note: str = "") -> bool:
    """Registra que hubo contacto con un lead (reset del timer de silencio)."""
    now = time.time()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id, status FROM leads WHERE user_id=? AND LOWER(name)=LOWER(?)",
            (user_id, name),
        ).fetchone()
        if not row:
            return False
        lead_id, current_status = row
        # Si estaba silencioso, volver a 'contacted'
        new_status = "contacted" if current_status == "silent" else current_status
        conn.execute(
            "UPDATE leads SET last_contact=?, status=? WHERE id=?",
            (now, new_status, lead_id),
        )
        if note:
            conn.execute(
                "INSERT INTO lead_activities (lead_id, user_id, activity, note, timestamp) "
                "VALUES (?,?,?,?,?)",
                (lead_id, user_id, "contact", note, now),
            )
        conn.commit()
        return True
    finally:
        conn.close()


def get_leads(user_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Lista todos los leads activos del usuario."""
    conn = _conn()
    try:
        if status:
            rows = conn.execute(
                "SELECT id, name, status, context, last_contact, created_at "
                "FROM leads WHERE user_id=? AND status=? ORDER BY last_contact DESC",
                (user_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, status, context, last_contact, created_at "
                "FROM leads WHERE user_id=? AND status NOT IN ('closed_won','closed_lost') "
                "ORDER BY last_contact DESC",
                (user_id,),
            ).fetchall()
        return [
            {
                "id": r[0], "name": r[1], "status": r[2],
                "context": r[3], "last_contact": r[4], "created_at": r[5],
                "days_silent": round((time.time() - r[4]) / 86400, 1),
            }
            for r in rows
        ]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Detección de silencio
# ---------------------------------------------------------------------------

def get_silent_leads(user_id: str, days: int = SILENCE_DAYS) -> List[Dict[str, Any]]:
    """Retorna leads que llevan más de N días sin contacto."""
    cutoff = time.time() - (days * 86400)
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, name, status, context, last_contact FROM leads "
            "WHERE user_id=? AND status IN ('contacted','proposal_sent','negotiating') "
            "AND last_contact < ? ORDER BY last_contact ASC",
            (user_id, cutoff),
        ).fetchall()
        return [
            {
                "id": r[0], "name": r[1], "status": r[2],
                "context": r[3], "last_contact": r[4],
                "days_silent": round((time.time() - r[4]) / 86400, 1),
            }
            for r in rows
        ]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Generación de mensaje de follow-up via LLM
# ---------------------------------------------------------------------------

def generate_followup(
    lead_name: str,
    context: str,
    days_silent: float,
    status: str,
    lang: str = "es",
    user_id: str = "",
) -> str:
    """
    Genera un mensaje de seguimiento personalizado para el lead.
    Inyecta preferencias conocidas del contacto si las hay.
    """
    # Cargar preferencias conocidas del contacto
    pref_context = ""
    if user_id and lead_name:
        try:
            from core.preference_tracker import build_contact_context
            pref_context = build_contact_context(user_id, lead_name)
        except Exception:
            pass

    status_labels = {
        "contacted": "primer contacto",
        "proposal_sent": "propuesta enviada",
        "negotiating": "en negociación",
        "silent": "sin respuesta",
    }
    status_label = status_labels.get(status, status)
    days_int = int(days_silent)

    # Detectar tono: personal o profesional según el contexto
    _BUSINESS_SIGNALS = [
        "propuesta", "presupuesto", "contrato", "cliente", "proyecto", "negocio",
        "diseño", "venta", "cobrar", "factura", "seguro", "servicio", "empresa",
        "proposal", "budget", "contract", "client", "business", "invoice", "service",
    ]
    ctx_lower = (context or "").lower()
    is_business = any(s in ctx_lower for s in _BUSINESS_SIGNALS)
    tone = "profesional y directo" if is_business else "informal y cercano, como amigo"
    tone_en = "professional and direct" if is_business else "casual and friendly, like a friend"

    if lang == "es":
        prompt = (
            f"Redacta un mensaje de seguimiento breve para {lead_name}. "
            f"Contexto: {context or 'persona conocida'}. "
            f"Tono: {tone}. Lleva {days_int} días sin responder. "
            + (f"{pref_context} " if pref_context else "") +
            f"Máximo 1-2 oraciones. Sin 'espero que estés bien' ni relleno. "
            f"Solo el texto del mensaje."
        )
    else:
        prompt = (
            f"Write a brief follow-up message for {lead_name}. "
            f"Context: {context or 'someone I know'}. "
            f"Tone: {tone_en}. No response for {days_int} days. "
            + (f"{pref_context} " if pref_context else "") +
            f"Max 1-2 sentences. No filler. Just the message text."
        )

    try:
        import os, httpx
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return _fallback_message(lead_name, days_int, lang)

        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 120,
                "temperature": 0.7,
            },
            timeout=8.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.debug("Follow-up LLM failed: %s", exc)
        return _fallback_message(lead_name, days_int, lang)


def _fallback_message(name: str, days: int, lang: str) -> str:
    """Mensaje de follow-up genérico cuando el LLM no está disponible."""
    if lang == "es":
        return f"Hola {name}, quería hacer seguimiento a nuestra conversación. ¿Pudiste revisar lo que hablamos?"
    return f"Hi {name}, just following up on our conversation. Did you get a chance to review what we discussed?"


# ---------------------------------------------------------------------------
# Formato para Telegram
# ---------------------------------------------------------------------------

def format_lead_list(leads: List[Dict]) -> str:
    """Formatea la lista de leads para mostrar en Telegram."""
    if not leads:
        return "No hay leads activos."

    status_icons = {
        "contacted": "🟡",
        "proposal_sent": "🔵",
        "negotiating": "🟢",
        "silent": "🔴",
        "closed_won": "✅",
        "closed_lost": "❌",
    }

    lines = ["📋 Leads activos:"]
    for lead in leads:
        icon = status_icons.get(lead["status"], "⚪")
        days = lead["days_silent"]
        silence_note = f" — {days:.0f}d sin respuesta" if days >= 1 else ""
        ctx = f" | {lead['context'][:40]}" if lead.get("context") else ""
        lines.append(f"  {icon} {lead['name']}{ctx}{silence_note}")
    return "\n".join(lines)
