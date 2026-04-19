"""
Vectrax Fact Memory — Segundo Cerebro
=========================================
Extrae y almacena hechos concretos de las conversaciones:
personas, cifras, fechas, deudas, cobros, reuniones, notas.

Cuando el usuario pregunta después, responde directo desde los hechos
almacenados sin pasar por el LLM.

Ejemplos:
  Input:  "Carlos me debe $500 del proyecto del logo"
  Stored: fact_type=money, subject=Carlos, value=$500, detail=proyecto del logo

  Query:  "¿Cuánto me debe Carlos?"
  Answer: "Carlos te debe $500 del proyecto del logo."

Creado: 2026-03-31
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger("vectrax.fact_memory")

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "user_memory.db",
)

_CREATE_FACTS = """
CREATE TABLE IF NOT EXISTS user_facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    fact_type   TEXT NOT NULL,
    subject     TEXT NOT NULL DEFAULT '',
    value       TEXT NOT NULL DEFAULT '',
    detail      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active',
    raw_input   TEXT NOT NULL DEFAULT '',
    timestamp   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_user ON user_facts(user_id);
CREATE INDEX IF NOT EXISTS idx_facts_subject ON user_facts(user_id, subject);

CREATE TABLE IF NOT EXISTS fact_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id     INTEGER NOT NULL,
    user_id     TEXT NOT NULL,
    old_value   TEXT NOT NULL DEFAULT '',
    new_value   TEXT NOT NULL DEFAULT '',
    old_status  TEXT NOT NULL DEFAULT '',
    new_status  TEXT NOT NULL DEFAULT '',
    change_type TEXT NOT NULL DEFAULT '',
    raw_input   TEXT NOT NULL DEFAULT '',
    timestamp   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_fact ON fact_history(fact_id);
"""


def _conn():
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=3)
    conn.executescript(_CREATE_FACTS)
    return conn


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------

@dataclass
class Fact:
    fact_type: str      # money, date, person, note, price, contact
    subject: str        # nombre de persona o tema
    value: str          # cantidad, fecha, dato
    detail: str         # contexto adicional
    raw_input: str      # texto original
    timestamp: float = 0.0


# ---------------------------------------------------------------------------
# Extractores — detectan hechos en texto natural
# ---------------------------------------------------------------------------

# Dinero: "Carlos me debe $500", "le cobro $80/hora a Juan"
_MONEY_PATTERNS = [
    # X me debe $Y / Z
    re.compile(
        r"(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)\s+me\s+debe\s+"
        r"[\$€]?\s*(\d[\d,.]*)\s*(?:(?:dol|peso|eur|usd|mxn)[\w]*)?\s*"
        r"(?:(?:del?|por)\s+(.{3,60}))?",
        re.IGNORECASE | re.UNICODE,
    ),
    # le cobro $Y a X / por Z
    re.compile(
        r"(?:le\s+)?cobro\s+[\$€]?\s*(\d[\d,.]*)\s*(?:/\s*hora)?\s*"
        r"(?:a\s+(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[a-záéíóúñ]+)?))\s*"
        r"(?:(?:por|del?)\s+(.{3,60}))?",
        re.IGNORECASE | re.UNICODE,
    ),
    # X owes me $Y
    re.compile(
        r"(\b[A-Z][a-z]+)\s+owes?\s+me\s+\$?\s*(\d[\d,.]*)\s*"
        r"(?:for\s+(.{3,60}))?",
        re.IGNORECASE,
    ),
    # mi precio es $Y / cobro $Y
    re.compile(
        r"(?:mi\s+precio|cobro|mi\s+tarifa)\s+(?:es\s+)?[\$€]?\s*(\d[\d,.]*)\s*"
        r"(?:/\s*hora|por\s+hora)?",
        re.IGNORECASE,
    ),
]

# Fechas/reuniones: "reunión el jueves con Ana", "cita el 15 de abril",
# "recuérdame que mañana tengo reunión", "tengo una junta mañana"

# Palabras temporales reconocidas (estrictas — no capturan "las 9" etc.)
_TIME_WORDS = (
    r"pasado\s+mañana|mañana|hoy|esta\s+(?:tarde|noche|semana)"
    r"|lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo"
    r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r"|tomorrow|today|tonight|next\s+(?:week|monday|tuesday|wednesday|thursday|friday)"
    r"|\d{1,2}\s+de\s+(?:enero|febrero|marzo|abril|mayo|junio"
    r"|julio|agosto|septiembre|octubre|noviembre|diciembre)"
)
_EVENT_WORDS = (
    r"reuni[oó]n|cita|junta|meeting|llamada|call|evento|entrega"
    r"|presentaci[oó]n|examen|turno|vuelo|viaje|compromiso"
)
# Sufijo opcional de hora: "a las 9", "a las 10:30", "at 3pm"
_TIME_SUFFIX = r"(?:\s+a\s+las?\s+\d{1,2}(?::\d{2})?(?:\s*[aApP][mM])?|\s+at\s+\d{1,2}(?::\d{2})?(?:\s*[aApP][mM])?)?"

_DATE_PATTERNS = [
    # 0: recuérdame que mañana tengo X [con Y]
    re.compile(
        r"(?:recu[eé]rda(?:me)?|acuerda(?:te)?|remember)\s+que\s+"
        r"(?:el\s+)?(" + _TIME_WORDS + r")" + _TIME_SUFFIX + r"\s+"
        r"(?:tengo|hay|es|tiene|there'?s?)\s+(?:una?\s+)?"
        r"(" + _EVENT_WORDS + r"|.{3,60}?)"
        r"(?:\s+con\s+(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+))?",
        re.IGNORECASE | re.UNICODE,
    ),
    # 1: recuérdame [la] reunión de mañana / recuerda la cita del lunes
    re.compile(
        r"(?:recu[eé]rda(?:me)?|acuerda(?:te)?|remember)\s+"
        r"(?:la\s+|el\s+|mi\s+)?(" + _EVENT_WORDS + r")\s+"
        r"(?:de(?:l)?\s+)?(" + _TIME_WORDS + r")" + _TIME_SUFFIX + r""
        r"(?:\s+con\s+(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+))?",
        re.IGNORECASE | re.UNICODE,
    ),
    # 2: mañana tengo reunión [a las 9] [con X] [de/sobre Y]
    re.compile(
        r"(?:el\s+)?(" + _TIME_WORDS + r")\s+"
        r"(?:tengo|hay)\s+(?:una?\s+)?(" + _EVENT_WORDS + r")"
        r"" + _TIME_SUFFIX + r""
        r"(?:\s+con\s+(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+))?"
        r"(?:\s+(?:de|sobre|en|por)\s+(.{3,60}))?",
        re.IGNORECASE | re.UNICODE,
    ),
    # 3: tengo [una] reunión mañana [a las 9] [con X] [de Y]
    re.compile(
        r"(?:tengo\s+(?:una?\s+)?)(" + _EVENT_WORDS + r")\s+"
        r"(?:el\s+)?(" + _TIME_WORDS + r")" + _TIME_SUFFIX + r""
        r"(?:\s+con\s+(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+))?"
        r"(?:\s+(?:de|sobre|en|por)\s+(.{3,60}))?",
        re.IGNORECASE | re.UNICODE,
    ),
    # 4: reunión/cita el TIEMPO [a las X] con PERSONA
    re.compile(
        r"(?:" + _EVENT_WORDS + r")\s+"
        r"(?:el\s+|para\s+(?:el\s+)?)?(" + _TIME_WORDS + r")" + _TIME_SUFFIX + r"\s+"
        r"(?:con\s+)(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[a-záéíóúñ]+)?)"
        r"(?:\s+(?:de|en|sobre)\s+(.{3,60}))?",
        re.IGNORECASE | re.UNICODE,
    ),
    # 5: el jueves tengo reunión con Y
    re.compile(
        r"(?:el\s+)?(" + _TIME_WORDS + r")\s+"
        r"(?:tengo\s+)?(?:" + _EVENT_WORDS + r")" + _TIME_SUFFIX + r"\s+"
        r"(?:con\s+)(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)",
        re.IGNORECASE | re.UNICODE,
    ),
]

# Personas/relaciones: "Ana es mi contadora", "Juan trabaja en Google"
_PERSON_PATTERNS = [
    re.compile(
        r"(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)\s+es\s+mi\s+(\w+(?:\s+\w+)?)",
        re.UNICODE,
    ),
    re.compile(
        r"(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)\s+trabaja\s+en\s+(.{3,40})",
        re.UNICODE,
    ),
    re.compile(
        r"(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)\s+(?:is\s+my|works\s+at)\s+(.{3,40})",
        re.IGNORECASE,
    ),
]

# Notas generales: "recordar que X", "nota: X", "importante: X"
_NOTE_PATTERNS = [
    re.compile(
        r"(?:recuerda\s+que|recordar\s+que|nota:\s*|importante:\s*|"
        r"remember\s+that|note:\s*)(.{5,200})",
        re.IGNORECASE,
    ),
]

# Compromisos informales de fecha: "nos vemos el lunes", "quedamos para el viernes"
_INFORMAL_DATE_PATTERNS = [
    re.compile(
        r"(?:nos\s+vemos|quedamos|hablamos|te\s+llamo|me\s+llamas?)\s+"
        r"(?:el\s+|para\s+(?:el\s+)?)?(" + _TIME_WORDS + r")" + _TIME_SUFFIX + r""
        r"(?:\s+con\s+(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+))?",
        re.IGNORECASE | re.UNICODE,
    ),
    re.compile(
        r"(?:quedar?\s+(?:en|para)|acordamos)\s+"
        r"(?:el\s+|para\s+(?:el\s+)?)?(" + _TIME_WORDS + r")" + _TIME_SUFFIX,
        re.IGNORECASE | re.UNICODE,
    ),
]

# Préstamos inversos: "le presté $200 a Pedro", "le di $500 a Juan"
_LEND_PATTERNS = [
    re.compile(
        r"(?:le\s+)?(?:prest[eé]|di|mand[eé]|transfeir[ií]|envi[eé])\s+"
        r"[\$€]?\s*(\d[\d,.]*)\s*"
        r"(?:a\s+(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[a-záéíóúñ]+)?))"
        r"(?:\s+(?:por|para|del?)\s+(.{3,60}))?",
        re.IGNORECASE | re.UNICODE,
    ),
]

# Decisiones tomadas: "decidí lanzar en mayo", "vamos a publicar el viernes"
_DECISION_PATTERNS = [
    re.compile(
        r"(?:decid[ií]|decidimos|vamos\s+a|planamos?|tenemos\s+previsto)\s+(.{5,120})"
        r"(?:\s+(?:el|para|en)\s+(" + _TIME_WORDS + r"))?",
        re.IGNORECASE | re.UNICODE,
    ),
]

# Señales LLM — palabras que sugieren un hecho incluso sin patrón regex claro
_LLM_FACT_SIGNALS = re.compile(
    r"\b(?:mañana|hoy|esta\s+(?:semana|tarde|noche)|lunes|martes|mi[eé]rcoles|jueves|viernes|"
    r"s[aá]bado|domingo|próximo|la\s+semana|el\s+mes|\d+\s+de\s+\w+|"
    r"prest[eé]|di\s+\$|debo|debe|cobrar|entregar|firmar|cerrar|reunir|hablar|llamar|"
    r"decid[ií]|decidimos|quedamos|acordamos|confirmamos|asignado|vence)",
    re.IGNORECASE,
)

# Cambios de estado: "Carlos ya me pagó", "Juan ya no me debe"
_STATE_CHANGE_PATTERNS = [
    # X ya me pagó / ya pagó X
    re.compile(
        r"(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)\s+ya\s+(?:me\s+)?pag[oó]",
        re.IGNORECASE | re.UNICODE,
    ),
    re.compile(
        r"ya\s+(?:me\s+)?pag[oó]\s+(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)",
        re.IGNORECASE | re.UNICODE,
    ),
    # X paid me / X already paid
    re.compile(
        r"(\b[A-Z][a-z]+)\s+(?:already\s+)?paid\s+me",
        re.IGNORECASE,
    ),
    # cancelé la reunión con X / se canceló lo de X
    re.compile(
        r"(?:cancel[eé]|se\s+cancel[oó])\s+(?:la\s+)?(?:reuni[oó]n|cita|junta)\s+(?:con\s+)?(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)",
        re.IGNORECASE | re.UNICODE,
    ),
    # X ya no me debe / ya no le debo a X
    re.compile(
        r"(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)\s+ya\s+no\s+me\s+debe",
        re.IGNORECASE | re.UNICODE,
    ),
    # ahora me debe $Y (update amount)
    re.compile(
        r"(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)\s+ahora\s+me\s+debe\s+[\$€]?\s*(\d[\d,.]*)",
        re.IGNORECASE | re.UNICODE,
    ),
]


def _extract_facts_llm(text: str) -> List[Fact]:
    """
    Nivel 2: extrae hechos usando LLM cuando el regex no detectó nada
    pero hay señales de que puede haber información relevante.

    Solo se activa si _LLM_FACT_SIGNALS coincide en el texto.
    Costo: ~50-100 tokens por llamada. Rápido (<2s con gpt-4o-mini).
    """
    if not _LLM_FACT_SIGNALS.search(text):
        return []

    prompt = (
        "Extrae hechos concretos de este mensaje. "
        'Retorna JSON array (o [] si no hay nada relevante). '
        'Formato: [{"type": "date|money|lend|person|decision|note", '
        '"subject": "nombre o tema", "value": "fecha/cantidad/dato", "detail": "contexto"}]\n'
        'Ejemplos:\n'
        '  "nos vemos el lunes" → [{"type":"date","subject":"_general","value":"lunes","detail":""}]\n'
        '  "le presté 200 a Pedro" → [{"type":"lend","subject":"Pedro","value":"200","detail":""}]\n'
        '  "decidí lanzar en mayo" → [{"type":"decision","subject":"lanzamiento","value":"mayo","detail":""}]\n'
        'NO extraer preguntas ni comandos. Solo hechos declarativos.\n'
        f'Mensaje: "{text[:300]}"\n'
        'JSON:'
    )

    try:
        import os, json
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return []
        import requests as _req
        resp = _req.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 200,
                "temperature": 0.0,
            },
            timeout=5,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        # Limpiar markdown si viene envuelto en ```json
        raw = raw.strip("`").lstrip("json").strip()
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        now = time.time()
        facts = []
        for item in data[:5]:  # máx 5 hechos por mensaje
            t = item.get("type", "note")
            subj = str(item.get("subject", ""))[:80]
            val = str(item.get("value", ""))[:80]
            det = str(item.get("detail", ""))[:120]
            if t == "lend":
                # Préstamo que yo hice — mapear a money con signo negativo
                facts.append(Fact("lend", subj, val, det, text, now))
            elif t and val:
                facts.append(Fact(t, subj, val, det, text, now))
        logger.info("LLM extracted %d facts from: %r", len(facts), text[:50])
        return facts
    except Exception as exc:
        logger.debug("LLM fact extraction failed: %s", exc)
        return []


def extract_facts(text: str) -> List[Fact]:
    """Extrae todos los hechos detectables del texto (regex + LLM hibíbrido)."""
    facts = []
    now = time.time()

    # Dinero
    for i, pat in enumerate(_MONEY_PATTERNS):
        m = pat.search(text)
        if m:
            groups = m.groups()
            if i == 0:  # X me debe $Y por Z
                facts.append(Fact("money", groups[0], groups[1], groups[2] or "", text, now))
            elif i == 1:  # cobro $Y a X por Z
                subject = groups[1] if groups[1] else ""
                facts.append(Fact("price", subject, groups[0], groups[2] or "", text, now))
            elif i == 2:  # X owes me $Y for Z
                facts.append(Fact("money", groups[0], groups[1], groups[2] or "", text, now))
            elif i == 3:  # mi precio es $Y
                facts.append(Fact("price", "_self", groups[0], "", text, now))

    # Fechas/reuniones
    for i, pat in enumerate(_DATE_PATTERNS):
        m = pat.search(text)
        if m:
            groups = m.groups()
            if i == 0:
                # "recuérdame que mañana tengo reunión [con X]"
                date_val = groups[0] if groups[0] else ""
                detail = groups[1] if len(groups) > 1 and groups[1] else ""
                person = groups[2] if len(groups) > 2 and groups[2] else ""
            elif i == 1:
                # "recuérdame la reunión de mañana [con X]"
                detail = groups[0] if groups[0] else ""
                date_val = groups[1] if len(groups) > 1 and groups[1] else ""
                person = groups[2] if len(groups) > 2 and groups[2] else ""
            elif i == 2:
                # "mañana tengo reunión [con X] [de Y]"
                date_val = groups[0] if groups[0] else ""
                detail = groups[1] if len(groups) > 1 and groups[1] else ""
                person = groups[2] if len(groups) > 2 and groups[2] else ""
                if len(groups) > 3 and groups[3]:
                    detail = f"{detail}, {groups[3]}"
            elif i == 3:
                # "tengo reunión mañana [con X] [de Y]"
                detail = groups[0] if groups[0] else "evento"
                date_val = groups[1] if len(groups) > 1 and groups[1] else ""
                person = groups[2] if len(groups) > 2 and groups[2] else ""
                if len(groups) > 3 and groups[3]:
                    detail = f"{detail}, {groups[3]}"
            elif i == 4 or i == 5:
                # Patrones clásicos: (fecha, persona, detalle)
                date_val = groups[0] if groups[0] else ""
                person = groups[1] if len(groups) > 1 and groups[1] else ""
                detail = groups[2] if len(groups) > 2 and groups[2] else ""
            else:
                date_val = groups[0] if groups[0] else ""
                person = groups[1] if len(groups) > 1 and groups[1] else ""
                detail = ""

            # Extraer hora del texto original y adjuntarla a date_val
            time_match = re.search(
                r"a\s+las?\s+(\d{1,2}(?::\d{2})?)(?:\s*([aApP][mM]))?"
                r"|at\s+(\d{1,2}(?::\d{2})?)(?:\s*([aApP][mM]))?",
                text, re.IGNORECASE,
            )
            if time_match:
                hour = time_match.group(1) or time_match.group(3) or ""
                ampm = time_match.group(2) or time_match.group(4) or ""
                if hour:
                    date_val = f"{date_val} a las {hour}{ampm}".strip()

            # Usar subject = persona o el tipo de evento como key de búsqueda
            subject = person if person else (detail if detail else "_general")
            facts.append(Fact("date", subject, date_val, detail, text, now))
            break  # Solo un match de fecha por input

    # Personas
    for pat in _PERSON_PATTERNS:
        m = pat.search(text)
        if m:
            facts.append(Fact("person", m.group(1), m.group(2).strip(), "", text, now))

    # Compromisos informales de fecha: "nos vemos el lunes", "quedamos el viernes"
    if not any(f.fact_type == "date" for f in facts):  # solo si no capturó fecha ya
        for pat in _INFORMAL_DATE_PATTERNS:
            m = pat.search(text)
            if m:
                groups = m.groups()
                date_val = groups[0] if groups else ""
                person = groups[1] if len(groups) > 1 and groups[1] else ""
                subject = person if person else "_general"
                facts.append(Fact("date", subject, date_val, "compromiso", text, now))
                break

    # Préstamos inversos: "le presté $200 a Pedro"
    for pat in _LEND_PATTERNS:
        m = pat.search(text)
        if m:
            groups = m.groups()
            amount = groups[0] if groups else ""
            person = groups[1] if len(groups) > 1 else ""
            detail = groups[2] if len(groups) > 2 and groups[2] else ""
            if amount and person:
                facts.append(Fact("lend", person, amount, detail, text, now))
                break

    # Decisiones: "decidí lanzar en mayo"
    for pat in _DECISION_PATTERNS:
        m = pat.search(text)
        if m:
            content = m.group(1).strip()[:100] if m.group(1) else ""
            date_hint = m.group(2).strip() if m.lastindex and m.lastindex >= 2 and m.group(2) else ""
            if content and len(content) > 5:
                facts.append(Fact("decision", "_self", date_hint or content[:40], content, text, now))
                break

    # Notas
    for pat in _NOTE_PATTERNS:
        m = pat.search(text)
        if m:
            facts.append(Fact("note", "", m.group(1).strip(), "", text, now))

    # Nivel 2: LLM si el regex no detectó nada pero hay señales
    if not facts:
        llm_facts = _extract_facts_llm(text)
        facts.extend(llm_facts)

    return facts


# ---------------------------------------------------------------------------
# Almacenamiento
# ---------------------------------------------------------------------------

def detect_state_changes(user_id: str, text: str) -> int:
    """
    Detecta cambios de estado en hechos existentes.
    Ej: 'Carlos ya me pagó' → deuda de Carlos pasa a status='paid'
    Retorna cantidad de cambios aplicados.
    """
    changes = 0
    now = time.time()

    for i, pat in enumerate(_STATE_CHANGE_PATTERNS):
        m = pat.search(text)
        if not m:
            continue

        name = m.group(1)
        new_amount = m.group(2) if m.lastindex and m.lastindex >= 2 else None

        # Determinar tipo de cambio
        if i <= 2:  # pagó patterns
            change_type = "paid"
            new_status = "paid"
            target_type = "money"
        elif i == 3:  # canceló reunión
            change_type = "cancelled"
            new_status = "cancelled"
            target_type = "date"
        elif i == 4:  # ya no me debe
            change_type = "cleared"
            new_status = "cleared"
            target_type = "money"
        elif i == 5:  # ahora me debe $Y (update)
            change_type = "updated"
            new_status = "active"
            target_type = "money"
        else:
            continue

        # Buscar hecho existente
        try:
            conn = _conn()
            row = conn.execute(
                "SELECT id, value, status FROM user_facts "
                "WHERE user_id = ? AND LOWER(subject) = LOWER(?) AND fact_type = ? "
                "AND status = 'active' ORDER BY timestamp DESC LIMIT 1",
                (user_id, name, target_type),
            ).fetchone()

            if row:
                fact_id, old_value, old_status = row
                final_value = new_amount if new_amount else "0"

                # Registrar en historial
                conn.execute(
                    "INSERT INTO fact_history "
                    "(fact_id, user_id, old_value, new_value, old_status, new_status, "
                    "change_type, raw_input, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (fact_id, user_id, old_value, final_value, old_status,
                     new_status, change_type, text, now),
                )

                # Actualizar hecho
                if change_type == "updated":
                    conn.execute(
                        "UPDATE user_facts SET value = ?, timestamp = ? WHERE id = ?",
                        (new_amount, now, fact_id),
                    )
                else:
                    conn.execute(
                        "UPDATE user_facts SET status = ?, timestamp = ? WHERE id = ?",
                        (new_status, now, fact_id),
                    )

                conn.commit()
                changes += 1
                logger.info(
                    "FACT STATE CHANGE | user=%s | %s | %s → %s | %s=$%s",
                    user_id[:20], name, old_status, new_status, change_type, old_value,
                )
            conn.close()
        except Exception as exc:
            logger.error("State change failed: %s", exc)

    return changes


def store_facts(user_id: str, text: str) -> int:
    """Extrae y almacena hechos del texto. Retorna cantidad almacenada."""
    # Primero detectar cambios de estado en hechos existentes
    state_changes = detect_state_changes(user_id, text)
    if state_changes > 0:
        return state_changes  # El cambio de estado es el hecho principal

    facts = extract_facts(text)
    if not facts:
        return 0

    try:
        conn = _conn()
        for f in facts:
            # Actualizar si ya existe un hecho con mismo subject y tipo
            existing = conn.execute(
                "SELECT id FROM user_facts WHERE user_id = ? AND fact_type = ? AND subject = ? "
                "ORDER BY timestamp DESC LIMIT 1",
                (user_id, f.fact_type, f.subject),
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE user_facts SET value = ?, detail = ?, raw_input = ?, timestamp = ? "
                    "WHERE id = ?",
                    (f.value, f.detail, f.raw_input, f.timestamp, existing[0]),
                )
            else:
                conn.execute(
                    "INSERT INTO user_facts (user_id, fact_type, subject, value, detail, raw_input, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (user_id, f.fact_type, f.subject, f.value, f.detail, f.raw_input, f.timestamp),
                )

            logger.info(
                "FACT stored | user=%s | type=%s | subject=%s | value=%s",
                user_id[:20], f.fact_type, f.subject, f.value,
            )

        conn.commit()
        conn.close()
        return len(facts)
    except Exception as exc:
        logger.error("Failed to store facts: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Consultas — respuesta directa desde hechos
# ---------------------------------------------------------------------------

# Queries de dinero
_MONEY_QUERY = re.compile(
    r"(?:cu[aá]nto\s+(?:me\s+)?debe\s+(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)"
    r"|(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)\s+me\s+debe"
    r"|how\s+much\s+(?:does\s+)?(\b[A-Z][a-z]+)\s+owe)",
    re.IGNORECASE | re.UNICODE,
)

_PRICE_QUERY = re.compile(
    r"(?:cu[aá]nto\s+(?:le\s+)?cobro\s+(?:a\s+)?(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?"
    r"|(?:mi|el)\s+(?:precio|tarifa)"
    r"|how\s+much\s+(?:do\s+I\s+)?charge)",
    re.IGNORECASE | re.UNICODE,
)

_DATE_QUERY = re.compile(
    r"(?:cu[aá]ndo\s+(?:es|tengo)\s+(?:lo\s+de\s+|la\s+(?:" + _EVENT_WORDS + r")\s+"
    r"(?:con\s+)?)?(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)"
    r"|when\s+is\s+(?:the\s+(?:meeting|call)\s+(?:with\s+)?)?(\b[A-Z][a-z]+))",
    re.IGNORECASE | re.UNICODE,
)

# Consultas generales de agenda: "qué tengo mañana", "qué reuniones tengo",
# "tengo algo pendiente", "qué hay para el lunes"
_GENERAL_DATE_QUERY = re.compile(
    r"(?:qu[eé]\s+(?:tengo|hay|tiene[s]?)\s+(?:para\s+)?(?:" + _TIME_WORDS + r")"
    r"|qu[eé]\s+(?:reuniones?|citas?|juntas?|eventos?|pendientes?)\s+tengo"
    r"|qu[eé]\s+tengo\s+pendiente"
    r"|tienes?\s+(?:algo|alguna)\s+(?:agendado|programado|pendiente)"
    r"|(?:algo|alguna\s+(?:" + _EVENT_WORDS + r"))\s+(?:para\s+)?(?:" + _TIME_WORDS + r")"
    r"|(?:te\s+)?(?:acuerdas|recuerdas)\s+(?:de\s+)?(?:(?:la|mi|alguna)\s+)?(?:" + _EVENT_WORDS + r")"
    r"|(?:la|mi)\s+(?:" + _EVENT_WORDS + r")\s*(?:de\s+)?(?:" + _TIME_WORDS + r")?"
    r"|a\s+qu[eé]\s+hora\s+(?:es|tengo)\s+(?:la\s+|el\s+|mi\s+)?(?:" + _EVENT_WORDS + r")"
    r"|what\s+(?:do\s+I\s+have|meetings?|events?)\s+(?:for\s+)?(?:" + _TIME_WORDS + r")?"
    r"|(?:my|the)\s+(?:meeting|appointment)"
    r"|what\s+time\s+is\s+(?:the\s+)?(?:meeting|appointment))",
    re.IGNORECASE | re.UNICODE,
)

_PERSON_QUERY = re.compile(
    r"(?:qui[eé]n\s+es\s+(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)"
    r"|qu[eé]\s+sabes\s+(?:de|sobre)\s+(\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)"
    r"|who\s+is\s+(\b[A-Z][a-z]+))",
    re.IGNORECASE | re.UNICODE,
)

_ALL_FACTS_QUERY = re.compile(
    r"(?:qu[eé]\s+(?:datos|hechos|informaci[oó]n)\s+tienes"
    r"|what\s+(?:facts|data)\s+do\s+you\s+have"
    r"|mis\s+(?:datos|hechos|notas)"
    r"|qu[eé]\s+recuerdas\s+(?:de\s+)?(?:todo|mis\s+datos))",
    re.IGNORECASE,
)


def query_facts(user_id: str, text: str) -> Optional[Dict[str, str]]:
    """
    Intenta responder una pregunta directamente desde los hechos almacenados.
    
    Retorna {"text": "respuesta", "source": "facts"} o None si no hay match.
    """
    # Dinero
    m = _MONEY_QUERY.search(text)
    if m:
        name = m.group(1) or m.group(2) or m.group(3) or ""
        if name:
            fact = _find_fact(user_id, "money", name)
            if fact:
                status = fact.get("status", "active")
                detail = f" del {fact['detail']}" if fact["detail"] else ""
                if status == "paid":
                    return {
                        "text": f"{fact['subject']} ya pagó. La deuda de ${fact['value']}{detail} está saldada.",
                        "source": "facts",
                    }
                elif status == "cleared":
                    return {
                        "text": f"{fact['subject']} ya no te debe nada.",
                        "source": "facts",
                    }
                return {
                    "text": f"{fact['subject']} te debe ${fact['value']}{detail}.",
                    "source": "facts",
                }

    # Precio/tarifa
    m = _PRICE_QUERY.search(text)
    if m:
        name = m.group(1) if m.lastindex and m.group(1) else ""
        if name:
            fact = _find_fact(user_id, "price", name)
        else:
            fact = _find_fact(user_id, "price", "_self") or _find_any_fact(user_id, "price")
        if fact:
            subject = fact["subject"]
            if subject == "_self":
                return {"text": f"Tu tarifa registrada es ${fact['value']}.", "source": "facts"}
            return {
                "text": f"Le cobras ${fact['value']} a {subject}.",
                "source": "facts",
            }

    # Fechas/reuniones (consulta específica con nombre)
    m = _DATE_QUERY.search(text)
    if m:
        name = m.group(1) or m.group(2) or ""
        if name:
            fact = _find_fact(user_id, "date", name)
            if fact:
                detail = f", {fact['detail']}" if fact["detail"] else ""
                subject = fact["subject"]
                if subject and subject != "_general":
                    return {
                        "text": f"Tienes {fact['value']} con {subject}{detail}.",
                        "source": "facts",
                    }
                return {
                    "text": f"Tienes {detail.lstrip(', ')} el {fact['value']}." if detail else f"Tienes algo el {fact['value']}.",
                    "source": "facts",
                }

    # Fechas/reuniones (consulta general: "qué tengo mañana", "qué reuniones tengo",
    # "a qué hora es la reunión")
    if _GENERAL_DATE_QUERY.search(text):
        facts = _get_facts_by_type(user_id, "date")
        if facts:
            parts = []
            for f in facts:
                subject = f["subject"]
                detail = f["detail"]
                date_val = f["value"]
                # Construir línea legible sin duplicados
                event_name = detail if detail and detail != subject else subject
                if not event_name or event_name == "_general":
                    event_name = ""
                # Si subject es persona (empieza con mayúscula y no es tipo de evento)
                is_person = (subject and subject[0].isupper()
                             and subject.lower() not in (
                                 "reunión", "reunion", "cita", "junta", "meeting",
                                 "llamada", "call", "evento", "entrega", "_general",
                             ))
                if is_person:
                    line = f"• {date_val}: {event_name} con {subject}" if event_name else f"• {date_val}: con {subject}"
                elif event_name:
                    line = f"• {date_val}: {event_name}"
                else:
                    line = f"• {date_val}"
                parts.append(line)
            header = "Tu agenda registrada:" if len(parts) > 1 else "Tienes esto pendiente:"
            return {"text": header + "\n" + "\n".join(parts), "source": "facts"}

    # Personas
    m = _PERSON_QUERY.search(text)
    if m:
        name = m.group(1) or m.group(2) or m.group(3) or ""
        if name:
            facts = _find_all_facts(user_id, name)
            if facts:
                parts = []
                for f in facts:
                    if f["fact_type"] == "person":
                        parts.append(f"{f['subject']} es tu {f['value']}.")
                    elif f["fact_type"] == "money":
                        parts.append(f"Te debe ${f['value']}.")
                    elif f["fact_type"] == "date":
                        parts.append(f"Tienes {f['value']} con {f['subject']}.")
                    elif f["fact_type"] == "price":
                        parts.append(f"Le cobras ${f['value']}.")
                if parts:
                    return {"text": " ".join(parts), "source": "facts"}

    # Todos los hechos
    if _ALL_FACTS_QUERY.search(text):
        facts = _get_all_facts(user_id)
        if facts:
            parts = []
            for f in facts:
                if f["fact_type"] == "money":
                    parts.append(f"• {f['subject']} te debe ${f['value']}")
                elif f["fact_type"] == "price":
                    s = f"tarifa: ${f['value']}" if f["subject"] == "_self" else f"cobras ${f['value']} a {f['subject']}"
                    parts.append(f"• {s}")
                elif f["fact_type"] == "date":
                    parts.append(f"• {f['value']} con {f['subject']}")
                elif f["fact_type"] == "person":
                    parts.append(f"• {f['subject']}: {f['value']}")
                elif f["fact_type"] == "note":
                    parts.append(f"• Nota: {f['value']}")
            return {"text": "\n".join(parts), "source": "facts"}

    return None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _find_fact(user_id: str, fact_type: str, subject: str) -> Optional[dict]:
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT fact_type, subject, value, detail, status FROM user_facts "
            "WHERE user_id = ? AND fact_type = ? AND LOWER(subject) = LOWER(?) "
            "ORDER BY timestamp DESC LIMIT 1",
            (user_id, fact_type, subject),
        ).fetchone()
        conn.close()
        if row:
            return {
                "fact_type": row[0], "subject": row[1],
                "value": row[2], "detail": row[3], "status": row[4],
            }
    except Exception:
        pass
    return None


def _find_any_fact(user_id: str, fact_type: str) -> Optional[dict]:
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT fact_type, subject, value, detail FROM user_facts "
            "WHERE user_id = ? AND fact_type = ? "
            "ORDER BY timestamp DESC LIMIT 1",
            (user_id, fact_type),
        ).fetchone()
        conn.close()
        if row:
            return {"fact_type": row[0], "subject": row[1], "value": row[2], "detail": row[3]}
    except Exception:
        pass
    return None


def _find_all_facts(user_id: str, subject: str) -> list:
    try:
        conn = _conn()
        rows = conn.execute(
            "SELECT fact_type, subject, value, detail FROM user_facts "
            "WHERE user_id = ? AND LOWER(subject) = LOWER(?) "
            "ORDER BY timestamp DESC LIMIT 10",
            (user_id, subject),
        ).fetchall()
        conn.close()
        return [{"fact_type": r[0], "subject": r[1], "value": r[2], "detail": r[3]} for r in rows]
    except Exception:
        return []


def _get_facts_by_type(user_id: str, fact_type: str) -> list:
    """Get all active facts of a specific type for user."""
    try:
        conn = _conn()
        rows = conn.execute(
            "SELECT fact_type, subject, value, detail FROM user_facts "
            "WHERE user_id = ? AND fact_type = ? AND status = 'active' "
            "ORDER BY timestamp DESC LIMIT 20",
            (user_id, fact_type),
        ).fetchall()
        conn.close()
        return [{"fact_type": r[0], "subject": r[1], "value": r[2], "detail": r[3]} for r in rows]
    except Exception:
        return []


def _get_all_facts(user_id: str) -> list:
    try:
        conn = _conn()
        rows = conn.execute(
            "SELECT fact_type, subject, value, detail FROM user_facts "
            "WHERE user_id = ? ORDER BY timestamp DESC LIMIT 20",
            (user_id,),
        ).fetchall()
        conn.close()
        return [{"fact_type": r[0], "subject": r[1], "value": r[2], "detail": r[3]} for r in rows]
    except Exception:
        return []
