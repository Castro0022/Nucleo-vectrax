"""
Vectrax Proactive Engine — Anticipación
==========================================
Vectrax habla primero cuando tiene algo relevante.
No espera que el usuario pregunte.

Principio: si el sistema sabe algo útil para el usuario en este momento,
lo dice. Sin que nadie lo pida.

Qué detecta:
  - Fechas próximas en user_facts (tipo 'date')
  - Tareas/metas en core_memory (categoría 'goal')
  - Deudas pendientes registradas (tipo 'money')

Frecuencia: corre cada 10 minutos en background thread del pipeline_worker.
Cooldown: máximo 1 mensaje proactivo por usuario cada 6 horas.

Privacidad: solo lee metadata y hechos del usuario — nunca genera datos nuevos.

Creado: 2026-04-02
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.proactive_engine")

_USER_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "user_memory.db",
)
_PROACTIVE_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "proactive_sent.db",
)

_CREATE_SENT = """
CREATE TABLE IF NOT EXISTS proactive_sent (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    fact_key    TEXT NOT NULL,
    sent_at     REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ps_user_fact ON proactive_sent(user_id, fact_key);
"""

COOLDOWN_HOURS = 6       # mín. horas entre mensajes al mismo usuario
CHECK_INTERVAL = 600     # segundos entre ejecuciones del motor (10 min)


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------

def _ps_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_PROACTIVE_DB), exist_ok=True)
    conn = sqlite3.connect(_PROACTIVE_DB, timeout=3)
    conn.executescript(_CREATE_SENT)
    return conn


def _already_sent(user_id: str, fact_key: str, hours: int = COOLDOWN_HOURS) -> bool:
    """True si ya se envió este fact_key al usuario en las últimas N horas."""
    cutoff = time.time() - (hours * 3600)
    try:
        conn = _ps_conn()
        row = conn.execute(
            "SELECT sent_at FROM proactive_sent WHERE user_id=? AND fact_key=? AND sent_at>?",
            (user_id, fact_key, cutoff),
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def _mark_sent(user_id: str, fact_key: str) -> None:
    try:
        conn = _ps_conn()
        conn.execute(
            "INSERT OR REPLACE INTO proactive_sent (user_id, fact_key, sent_at) VALUES (?,?,?)",
            (user_id, fact_key, time.time()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _user_cooldown_active(user_id: str) -> bool:
    """True si el usuario recibió algún mensaje proactivo en las últimas COOLDOWN_HOURS."""
    cutoff = time.time() - (COOLDOWN_HOURS * 3600)
    try:
        conn = _ps_conn()
        row = conn.execute(
            "SELECT 1 FROM proactive_sent WHERE user_id=? AND sent_at>? LIMIT 1",
            (user_id, cutoff),
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Detección de fechas en texto
# ---------------------------------------------------------------------------

_DAY_NAMES_ES = {
    "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2,
    "jueves": 3, "viernes": 4, "sábado": 5, "sabado": 5, "domingo": 6,
}

_DAY_NAMES_EN = {
    "monday": 0, "tuesday": 1, "wednesday": 2,
    "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
}


def _days_until(text: str) -> Optional[int]:
    """
    Estima cuántos días faltan para la fecha mencionada en text.
    Retorna 0=hoy, 1=mañana, 2-6=días de semana próximos, o None si no detecta.
    """
    t = text.lower().strip()
    today = datetime.now()
    today_weekday = today.weekday()  # 0=lunes

    # Hoy / mañana
    if re.search(r"\bhoy\b|today\b", t):
        return 0
    if re.search(r"\bma[ñn]ana\b|tomorrow\b", t):
        return 1

    # Nombre de día
    all_days = {**_DAY_NAMES_ES, **_DAY_NAMES_EN}
    for name, weekday in all_days.items():
        if re.search(rf"\b{re.escape(name)}\b", t):
            diff = (weekday - today_weekday) % 7
            # diff==0 significa hoy — notificar hoy (el cooldown previene spam)
            return diff

    # "el N" — número de día del mes
    m = re.search(r"\bel\s+(\d{1,2})\b", t)
    if m:
        day_num = int(m.group(1))
        try:
            target = today.replace(day=day_num)
            if target < today:
                # mes siguiente
                if today.month == 12:
                    target = target.replace(year=today.year + 1, month=1)
                else:
                    target = target.replace(month=today.month + 1)
            return (target.date() - today.date()).days
        except Exception:
            pass

    return None


# ---------------------------------------------------------------------------
# Escaneo de hechos
# ---------------------------------------------------------------------------

def _scan_date_facts(user_id: str) -> List[Dict[str, Any]]:
    """Busca facts de tipo 'date' con fechas próximas (0-2 días)."""
    triggers = []
    try:
        conn = sqlite3.connect(_USER_DB, timeout=2)
        rows = conn.execute(
            "SELECT id, fact_type, subject, value, detail FROM user_facts "
            "WHERE user_id=? AND fact_type='date' AND status='active'",
            (user_id,),
        ).fetchall()
        conn.close()

        for fid, ftype, subject, value, detail in rows:
            # Buscar fecha en value o detail
            text_to_check = f"{value} {detail}".strip()
            days = _days_until(text_to_check)
            if days is not None and 0 <= days <= 2:
                triggers.append({
                    "fact_id": fid,
                    "subject": subject,
                    "value": value,
                    "detail": detail,
                    "days_until": days,
                })
    except Exception as exc:
        logger.debug("scan_date_facts failed: %s", exc)
    return triggers


def _scan_goal_facts(user_id: str) -> List[Dict[str, Any]]:
    """Busca metas en core_memory con palabras clave de acción próxima."""
    triggers = []
    try:
        conn = sqlite3.connect(_USER_DB, timeout=2)
        rows = conn.execute(
            "SELECT id, content FROM core_memory WHERE user_id=? AND category='goal'",
            (user_id,),
        ).fetchall()
        conn.close()

        for gid, content in rows:
            days = _days_until(content)
            if days is not None and 0 <= days <= 1:
                triggers.append({
                    "fact_id": f"goal_{gid}",
                    "content": content,
                    "days_until": days,
                })
    except Exception as exc:
        logger.debug("scan_goal_facts failed: %s", exc)
    return triggers


# ---------------------------------------------------------------------------
# Generación de mensaje proactivo
# ---------------------------------------------------------------------------

def _build_message(user_id: str, triggers: List[Dict], lang: str = "es") -> Optional[str]:
    """Construye un mensaje proactivo con tono humano y directo."""
    if not triggers:
        return None

    # Obtener nombre del usuario para personalizar
    name = ""
    try:
        conn = sqlite3.connect(_USER_DB, timeout=2)
        row = conn.execute(
            "SELECT name FROM profiles WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.close()
        if row and row[0]:
            name = row[0].split()[0]  # solo primer nombre
    except Exception:
        pass

    lines = []
    for t in triggers[:2]:
        days = t.get("days_until", 0)

        if "content" in t:
            content = t["content"].rstrip(".")
            if days == 0:
                msg = f"Hoy tenías esto pendiente: {content}"
            elif days == 1:
                msg = f"Oye, mañana: {content}"
            else:
                msg = f"En {days} días: {content}"
        else:
            subj = t.get("subject", "")
            val = t.get("value", "")
            detail = t.get("detail", "")
            desc = subj if subj else val
            extra = val if subj else ""
            if detail:
                extra = f"{extra} ({detail})".strip(" ()")

            if days == 0:
                msg = f"Hoy tenías algo con {desc}"
                if extra:
                    msg += f" — {extra}"
            elif days == 1:
                msg = f"Oye, mañana tienes algo con {desc}"
                if extra:
                    msg += f" — {extra}"
            else:
                msg = f"El {_day_label(days)}: {desc}"
                if extra:
                    msg += f" — {extra}"

        lines.append(msg)

    if not lines:
        return None

    # Personalizar con nombre si está disponible
    body = "\n".join(lines)
    if name:
        return f"{name}, {body[0].lower()}{body[1:]}"
    return body


def _day_label(days: int) -> str:
    """Convierte N días en etiqueta legible."""
    today = datetime.now()
    target = today + timedelta(days=days)
    day_names = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    return day_names[target.weekday()]


# ---------------------------------------------------------------------------
# API principal
# ---------------------------------------------------------------------------

def _scan_connections(user_id: str) -> List[Dict[str, Any]]:
    """
    Detecta conexiones entre facts distintos que el usuario guardó por separado.

    Ejemplo:
      - user_facts: "Carlos" es "cliente" Y "Carlos" debe "500"
      → conecta: "Tienes una reunión con Carlos y le debes cobrar $500"

      - user_facts: "Ana" es "novia" Y "Ana" tiene fecha "jueves"
      → conecta: "Mañana es jueves — tenías algo con Ana"
    """
    connections = []
    try:
        conn = sqlite3.connect(_USER_DB, timeout=2)
        # Agrupar facts por subject (nombre de persona/cosa)
        rows = conn.execute(
            "SELECT fact_type, subject, value, detail, id FROM user_facts "
            "WHERE user_id=? AND status='active' AND subject != ''",
            (user_id,),
        ).fetchall()
        conn.close()

        # Agrupar por subject
        by_subject: Dict[str, List] = {}
        for ft, subj, val, det, fid in rows:
            key = subj.lower().strip()
            if key not in by_subject:
                by_subject[key] = []
            by_subject[key].append({"type": ft, "value": val, "detail": det, "id": fid})

        # Buscar subjects con múltiples tipos (ej: persona con fecha Y deuda)
        for subj, facts in by_subject.items():
            if len(facts) < 2:
                continue
            types = {f["type"] for f in facts}

            # Conexión: persona con fecha próxima + otro hecho relevante
            date_facts = [f for f in facts if f["type"] == "date"]
            other_facts = [f for f in facts if f["type"] != "date"]

            for df in date_facts:
                days = _days_until(f"{df['value']} {df['detail']}")
                if days is not None and 0 <= days <= 1:
                    # Hay fecha próxima + contexto adicional
                    for of in other_facts[:1]:  # solo el primero
                        connections.append({
                            "fact_id": f"conn_{df['id']}_{of['id']}",
                            "subject": subj.capitalize(),
                            "date_val": df["value"],
                            "context_type": of["type"],
                            "context_val": of["value"],
                            "days_until": days,
                            "is_connection": True,
                        })
    except Exception as exc:
        logger.debug("scan_connections failed: %s", exc)
    return connections


def _format_connection(c: Dict, name: str = "") -> str:
    """Formatea un mensaje de conexión entre dos facts."""
    subj = c["subject"]
    days = c["days_until"]
    ctx_type = c["context_type"]
    ctx_val = c["context_val"]

    when = "Hoy" if days == 0 else "Mañana"

    # Personalizar según tipo de contexto
    if ctx_type == "money":
        extra = f"le debes cobrar ${ctx_val}" if ctx_val else "hay un tema de dinero pendiente"
    elif ctx_type == "person":
        extra = f"es tu {ctx_val}" if ctx_val else ""
    elif ctx_type == "task":
        extra = ctx_val
    else:
        extra = f"{ctx_type}: {ctx_val}" if ctx_val else ""

    base = f"{when} tienes algo con {subj}"
    if extra:
        base += f" — {extra}"

    if name:
        return f"{name}, {base[0].lower()}{base[1:]}"
    return base


def _scan_silent_leads(user_id: str, lang: str = "es") -> Optional[str]:
    """
    Detecta leads que llevan más de SILENCE_DAYS sin respuesta
    y genera el mensaje de follow-up correspondiente.
    Retorna el mensaje proactivo o None si no hay leads silenciosos.
    """
    try:
        from core.lead_tracker import get_silent_leads, generate_followup, update_lead_status
        silent = get_silent_leads(user_id)
        if not silent:
            return None

        # Tomar el más urgente (el que lleva más tiempo)
        lead = silent[0]
        followup_msg = generate_followup(
            lead_name=lead["name"],
            context=lead["context"],
            days_silent=lead["days_silent"],
            status=lead["status"],
            lang=lang,
        )

        # Marcar como 'silent' en el tracker
        update_lead_status(user_id, lead["name"], "silent")

        days = int(lead["days_silent"])
        if lang == "es":
            return (
                f"🔴 Seguimiento pendiente: {lead['name']} ({days}d)\n\n"
                f"Mensaje sugerido:\n“{followup_msg}”"
            )
        return (
            f"🔴 Follow-up needed: {lead['name']} ({days}d)\n\n"
            f"Suggested message:\n“{followup_msg}”"
        )
    except Exception as exc:
        logger.debug("_scan_silent_leads failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Priority filter — only interrupt for what truly matters
# ---------------------------------------------------------------------------

# Keywords that indicate HIGH PRIORITY (always interrupt)
_HIGH_PRIORITY_KEYWORDS = re.compile(
    r"(?:"
    # Financial
    r"\b(?:dinero|money|pago|cobr|deuda|debt|factura|invoice|sueldo|salary)"
    r"|\b(?:inversi[oó]n|investment|bitcoin|btc|trading|cripto|crypto)"
    r"|\b(?:presupuesto|budget|ganancia|profit|p[eé]rdida|loss)\b"
    # Relationships (Joy, family)
    r"|\b(?:joy|joycelin|novia|girlfriend|pareja|partner)"
    r"|\b(?:familia|family|hijo|hija|esposa|wife|madre|padre)\b"
    # Health / urgency
    r"|\b(?:doctor|m[eé]dico|cita m[eé]dica|hospital|salud|health)"
    r"|\b(?:urgente|urgent|emergencia|emergency|deadline|vencimiento)\b"
    # Vectrax business
    r"|\b(?:vectrax|cliente|client|demo|lanzamiento|launch)\b"
    r")",
    re.IGNORECASE,
)

# Keywords that indicate LOW PRIORITY (don't interrupt)
_LOW_PRIORITY_KEYWORDS = re.compile(
    r"(?:"
    r"\b(?:gym|entreno|dieta|pel[ií]cula|movie|serie|libro|book)"
    r"|\b(?:comprar|buy|tienda|store|supermercado)\b"
    r")",
    re.IGNORECASE,
)


def _is_high_priority(trigger: Dict) -> bool:
    """Check if a trigger is important enough to interrupt the user."""
    text = ""
    for key in ("subject", "value", "detail", "content", "context_val"):
        text += " " + str(trigger.get(key, ""))

    # High priority keywords always pass
    if _HIGH_PRIORITY_KEYWORDS.search(text):
        return True

    # Low priority keywords never pass
    if _LOW_PRIORITY_KEYWORDS.search(text):
        return False

    # Same-day events always pass (urgency)
    if trigger.get("days_until", 99) == 0:
        return True

    # Connections (cross-fact) are inherently higher value
    if trigger.get("is_connection"):
        return True

    # Default: don't interrupt for generic reminders
    return False


def run_proactive_scan(tg_send_fn) -> int:
    """
    Escanea todos los usuarios activos y envía mensajes proactivos si corresponde.

    Only sends messages for HIGH PRIORITY triggers:
    - Financial events (money, trading, invoices)
    - Key relationships (Joy, family)
    - Health/urgency (doctor, deadline, emergency)
    - Vectrax business (clients, launches)
    - Same-day events (anything due today)

    Args:
        tg_send_fn: función (chat_id: int, text: str) → bool para enviar a Telegram

    Returns:
        Número de mensajes enviados.
    """
    sent = 0
    try:
        conn = sqlite3.connect(_USER_DB, timeout=2)
        # Usuarios activos con Telegram ID
        users = conn.execute(
            "SELECT DISTINCT user_id FROM profiles WHERE user_id LIKE 'tg:%'"
        ).fetchall()
        # Obtener idioma por usuario
        lang_map = dict(conn.execute(
            "SELECT user_id, language FROM profiles WHERE user_id LIKE 'tg:%'"
        ).fetchall())
        conn.close()
    except Exception as exc:
        logger.debug("proactive_scan: can't load users: %s", exc)
        return 0

    for (user_id,) in users:
        try:
            if _user_cooldown_active(user_id):
                continue

            # Extraer chat_id numérico del user_id
            if not user_id.startswith("tg:"):
                continue
            try:
                chat_id = int(user_id.split(":", 1)[1])
            except ValueError:
                continue

            # Escanear hechos relevantes
            # Prioridad: conexiones > fechas > metas
            connections = _scan_connections(user_id)
            date_triggers = _scan_date_facts(user_id)
            goal_triggers = _scan_goal_facts(user_id)

            # Combinar: conexiones primero (son más ricas)
            all_triggers = connections + [
                t for t in date_triggers
                if not any(str(t["fact_id"]) in c["fact_id"] for c in connections)
            ] + goal_triggers

            if not all_triggers:
                continue

            # Filter by priority — only interrupt for what matters
            all_triggers = [t for t in all_triggers if _is_high_priority(t)]
            if not all_triggers:
                continue

            # Filtrar los ya enviados
            new_triggers = []
            for t in all_triggers:
                fkey = f"{t.get('fact_id', '')}:{t.get('days_until', 0)}"
                if not _already_sent(user_id, fkey):
                    new_triggers.append((t, fkey))

            if not new_triggers:
                continue
            lang = lang_map.get(user_id, "es") or "es"

            # Verificar leads silenciosos (prioridad alta)
            lead_msg = _scan_silent_leads(user_id, lang=lang)
            if lead_msg and not _already_sent(user_id, f"lead_silent_{user_id}", hours=24):
                ok = tg_send_fn(chat_id, lead_msg)
                if ok:
                    _mark_sent(user_id, f"lead_silent_{user_id}")
                    sent += 1
                    logger.info("Lead silent alert sent | user=%s", user_id[:20])
                    continue  # un mensaje por ciclo

            msg_parts = []
            # Mensajes de conexión tienen su propio formateador
            conn_triggers = [t for t, _ in new_triggers if t.get("is_connection")]
            simple_triggers = [t for t, _ in new_triggers if not t.get("is_connection")]

            # Obtener nombre
            _name = ""
            try:
                _dbc = sqlite3.connect(_USER_DB, timeout=2)
                _row = _dbc.execute(
                    "SELECT name FROM profiles WHERE user_id=?", (user_id,)
                ).fetchone()
                _dbc.close()
                if _row and _row[0]:
                    _name = _row[0].split()[0]
            except Exception:
                pass

            msg_parts = []
            for ct in conn_triggers[:1]:  # máximo 1 conexión
                msg_parts.append(_format_connection(ct, name=_name))
            if not msg_parts and simple_triggers:
                msg_parts.append(_build_message(user_id, simple_triggers[:2], lang=lang) or "")

            msg = "\n".join(p for p in msg_parts if p)
            if not msg:
                continue

            ok = tg_send_fn(chat_id, msg)
            if ok:
                for _, fkey in new_triggers:
                    _mark_sent(user_id, fkey)
                sent += 1
                logger.info(
                    "Proactive message sent | user=%s | triggers=%d",
                    user_id[:20], len(new_triggers),
                )

        except Exception as exc:
            logger.debug("proactive_scan user %s failed: %s", user_id[:20], exc)

    return sent
