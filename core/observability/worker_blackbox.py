"""
core/observability/worker_blackbox.py — Caja Negra del Worker.

Cuando el supervisor detecta un worker colgado (heartbeat stale),
ANTES de matarlo captura un snapshot forense completo:

  - timestamp exacto
  - worker_id (PID)
  - último heartbeat y su antigüedad
  - últimos 100 logs del worker
  - última tarea activa (msg_id, user_id, content, duración)
  - uso de CPU/RAM/disco/red
  - tamaño de cola pendiente
  - llamadas externas pendientes (API timeouts)
  - errores recientes (últimas 10 líneas de error)
  - traceback si existe
  - acción tomada (kill, restart, etc.)

Después del restart, genera un diagnóstico root_cause con:
  - causa probable
  - nivel de confianza (0-1)
  - evidencia que soporta la conclusión
  - acción recomendada
  - solución propuesta

Causas probables:
  tarea_bloqueada | timeout_externo | memoria_alta | error_db |
  loop_infinito | proceso_muerto | cola_saturada | fallo_red

Persistencia: vault/worker_incidents.jsonl
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.blackbox")

_VAULT = os.environ.get(
    "VECTRAX_VAULT_DIR",
    os.path.join(os.path.expanduser("~"), "Vectrax", "vault"),
)
_INCIDENTS_FILE = os.path.join(_VAULT, "worker_incidents.jsonl")
_RUNTIME = os.path.join(os.path.expanduser("~"), ".vectrax")
_WORKER_LOG = os.path.join(_RUNTIME, "worker.log")
_GATEWAY_LOG = os.path.join(_RUNTIME, "gateway.log")
_MAX_INCIDENTS = 200  # retain last N incidents


# ── Snapshot capture ──────────────────────────────────────────────────

def capture_incident(
    worker_name: str,
    pid: int,
    heartbeat_age: float,
    trigger: str = "heartbeat_stale",
) -> Dict[str, Any]:
    """Capture complete forensic snapshot BEFORE killing the worker.

    Must be called from the supervisor BEFORE process.kill().
    Returns the incident dict (also persisted to disk).
    """
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    incident_id = f"INC-{int(time.time())}-{worker_name[:3].upper()}"

    incident: Dict[str, Any] = {
        "incident_id": incident_id,
        "timestamp": ts,
        "worker_name": worker_name,
        "worker_pid": pid,
        "trigger": trigger,
        "heartbeat_age_s": round(heartbeat_age, 1),
    }

    # 1. Last 100 log lines
    incident["last_logs"] = _read_last_logs(worker_name, 100)

    # 2. Active task from queue
    incident["active_task"] = _get_active_task()

    # 3. CPU / RAM / Disk
    incident["resources"] = _get_resources(pid)

    # 4. Queue state
    incident["queue"] = _get_queue_state()

    # 5. Recent errors (last 10 from logs)
    incident["recent_errors"] = _extract_errors(incident["last_logs"], 10)

    # 6. External API calls (pending/timeout)
    incident["external_calls"] = _detect_external_calls(incident["last_logs"])

    # 7. Traceback (if found in recent logs)
    incident["traceback"] = _extract_traceback(incident["last_logs"])

    # 8. Action taken
    incident["action_taken"] = "kill_and_restart"

    # Persist
    _save_incident(incident)

    # Log to observation ledger
    _log_to_observation_ledger(incident)

    logger.warning(
        "BLACKBOX | %s captured for %s (PID %d) | trigger=%s | hb_age=%.0fs",
        incident_id, worker_name, pid, trigger, heartbeat_age,
    )

    return incident


# ── Root cause diagnosis ──────────────────────────────────────────────

def diagnose_incident(incident: Dict[str, Any]) -> Dict[str, Any]:
    """Generate root cause diagnosis after restart.

    Analyzes the captured snapshot to determine probable cause,
    confidence level, supporting evidence, and recommended action.
    Returns diagnosis dict (also appended to the incident file).
    """
    diag: Dict[str, Any] = {
        "incident_id": incident.get("incident_id", "?"),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    pos_evidence: List[str] = []  # supports the diagnosis
    neg_evidence: List[str] = []  # contradicts or weakens it
    cause = "desconocido"
    confidence = 0.5  # start neutral
    action = "monitorear"
    solution = ""

    task = incident.get("active_task", {})
    res = incident.get("resources", {})
    queue = incident.get("queue", {})
    errors = incident.get("recent_errors", [])
    ext_calls = incident.get("external_calls", {})
    tb = incident.get("traceback", "")
    hb_age = incident.get("heartbeat_age_s", 0)
    ram_mb = res.get("ram_mb", 0)
    pending_q = queue.get("pending", 0)
    task_duration = task.get("duration_s", 0)
    has_net_errors = any("connect" in e.lower() or "network" in e.lower() or "ssl" in e.lower() for e in errors)
    db_errors = [e for e in errors if "sqlite" in e.lower() or "database" in e.lower() or "locked" in e.lower()]

    # --- Rule-based diagnosis with positive/negative evidence ---

    # 1. Task blocked
    if task_duration and task_duration > 15:
        cause = "tarea_bloqueada"
        confidence = 0.8
        pos_evidence.append(f"Tarea activa por {task_duration:.0f}s (límite 20s)")
        action = "verificar contenido del mensaje que causó el bloqueo"
        solution = "Agregar timeout más estricto en _process_one() o investigar qué módulo se cuelga"
    else:
        neg_evidence.append("Sin tarea activa bloqueada")

    # 2. External API timeout
    has_ext = ext_calls.get("pending_count", 0) > 0
    has_timeout_err = "timeout" in " ".join(errors).lower()
    if has_ext or has_timeout_err:
        if cause == "desconocido":
            cause = "timeout_externo"
            confidence = 0.75
        else:
            confidence = min(confidence + 0.1, 0.95)
        pending_apis = ext_calls.get("pending_apis", [])
        pos_evidence.append(f"APIs con timeout/error reciente: {pending_apis}")
        if has_timeout_err:
            pos_evidence.append("Palabra 'timeout' en errores recientes")
        action = "verificar conectividad a APIs externas (OpenAI, Telegram, eToro)"
        solution = "Reducir timeouts de httpx o agregar circuit breaker para API problemática"
    else:
        neg_evidence.append("Sin APIs externas pendientes ni timeouts")

    # 3. High memory
    if ram_mb > 500:
        if cause == "desconocido":
            cause = "memoria_alta"
            confidence = 0.7
        pos_evidence.append(f"RAM: {ram_mb}MB (alto para un worker)")
        action = "investigar memory leak"
        solution = "Analizar objetos en heap con tracemalloc o reiniciar worker periódicamente"
    elif ram_mb > 0:
        neg_evidence.append(f"RAM normal: {ram_mb}MB")

    # 4. Database error
    if db_errors:
        if cause == "desconocido":
            cause = "error_db"
            confidence = 0.8
        pos_evidence.append(f"Errores de DB: {db_errors[:2]}")
        action = "verificar integridad de SQLite y locks"
        solution = "Ejecutar PRAGMA integrity_check en las DBs del vault"
    else:
        neg_evidence.append("Sin errores de base de datos")

    # 5. Queue saturated
    if pending_q > 10:
        if cause == "desconocido":
            cause = "cola_saturada"
            confidence = 0.6
        pos_evidence.append(f"Cola con {pending_q} mensajes pendientes")
        action = "verificar por qué se acumulan mensajes"
        solution = "Aumentar CONCURRENT workers o investigar tarea lenta"
    else:
        neg_evidence.append(f"Cola limpia: {pending_q} pendientes")

    # 6. Network failure
    if has_net_errors:
        net_err_lines = [e for e in errors if "connect" in e.lower() or "network" in e.lower() or "ssl" in e.lower()]
        if cause == "desconocido":
            cause = "fallo_red"
            confidence = 0.7
        pos_evidence.append(f"Errores de red: {net_err_lines[:2]}")
        action = "verificar conectividad del servidor"
        solution = "Revisar DNS, firewall, y estabilidad de red Vultr"
    else:
        neg_evidence.append("Sin errores de red confirmados")

    # 7. Traceback
    if tb:
        if cause == "desconocido":
            cause = "excepcion_no_capturada"
            confidence = 0.85
        pos_evidence.append(f"Traceback encontrado: {tb[:100]}")
        action = "corregir la excepción en el código"
        solution = f"Revisar: {tb[:200]}"
    else:
        neg_evidence.append("Sin traceback en logs")

    # 8. Heartbeat very stale
    if hb_age > 60 and cause == "desconocido":
        cause = "loop_infinito"
        confidence = 0.5
        pos_evidence.append(f"Heartbeat sin actualizar por {hb_age:.0f}s")
        action = "agregar watchdog interno en el worker"
        solution = "Añadir alarma SIGALRM o timeout por tarea en pipeline_worker"

    # 9. No indicators
    if cause == "desconocido":
        cause = "proceso_muerto"
        confidence = 0.3
        pos_evidence.append("Sin indicadores claros de causa raíz")
        action = "monitorear si se repite"
        solution = "Si recurre, habilitar profiling y analizar patrón"

    # --- Adjust confidence by evidence balance ---
    # More negative evidence = less certain about the diagnosis
    n_pos = len(pos_evidence)
    n_neg = len(neg_evidence)
    if n_neg > n_pos and confidence > 0.4:
        confidence -= 0.1 * min(n_neg - n_pos, 3)  # max -0.3 penalty
        confidence = max(confidence, 0.2)
    elif n_pos > n_neg + 2:
        confidence = min(confidence + 0.05, 0.95)  # slight boost

    diag["causa_probable"] = cause
    diag["confianza"] = round(confidence, 2)
    diag["evidencia_positiva"] = pos_evidence
    diag["evidencia_negativa"] = neg_evidence
    diag["evidencia"] = pos_evidence  # backward compat
    diag["accion_recomendada"] = action
    diag["solucion_propuesta"] = solution
    diag["tiempo_recuperacion_s"] = 0  # filled by caller after restart

    # Persist diagnosis
    _save_incident(diag)
    _log_diagnosis_to_ledger(diag)

    # Send Telegram alert
    _alert_creator(incident, diag)

    logger.info(
        "BLACKBOX DIAGNOSIS | %s | causa=%s conf=%.0f%% | %s",
        diag["incident_id"], cause, confidence * 100, action,
    )

    return diag


# ── Data collectors ───────────────────────────────────────────────────

def _read_last_logs(worker_name: str, n: int) -> List[str]:
    log_path = _WORKER_LOG if "worker" in worker_name else _GATEWAY_LOG
    try:
        if not os.path.exists(log_path):
            return []
        with open(log_path, "rb") as f:
            # Read last 50KB efficiently
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 50000))
            content = f.read().decode("utf-8", errors="replace")
        lines = content.strip().split("\n")
        return lines[-n:]
    except Exception:
        return []


def _get_active_task() -> Dict[str, Any]:
    """Find the currently processing task from the message queue."""
    try:
        queue_db = os.path.join(_VAULT, "message_queue.db")
        if not os.path.exists(queue_db):
            return {}
        conn = sqlite3.connect(queue_db, timeout=2)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, user_id, content, chat_id, created_at "
            "FROM queue WHERE status = 'processing' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row:
            return {"status": "no_active_task"}
        created = row["created_at"] or 0
        duration = time.time() - created if created else 0
        return {
            "msg_id": row["id"],
            "user_id": row["user_id"],
            "content": (row["content"] or "")[:100],
            "chat_id": row["chat_id"],
            "duration_s": round(duration, 1),
            "created_at": created,
        }
    except Exception as exc:
        return {"error": str(exc)}


def _get_resources(pid: int) -> Dict[str, Any]:
    """Collect CPU/RAM/disk usage."""
    res: Dict[str, Any] = {}
    try:
        # RAM from /proc
        proc_status = Path(f"/proc/{pid}/status")
        if proc_status.exists():
            for line in proc_status.read_text().split("\n"):
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    res["ram_mb"] = round(kb / 1024, 1)
                elif line.startswith("Threads:"):
                    res["threads"] = int(line.split()[1])
    except Exception:
        pass

    # System-wide
    try:
        import shutil
        total, used, free = shutil.disk_usage("/")
        res["disk_used_pct"] = round(used / total * 100, 1)
        res["disk_free_gb"] = round(free / (1024**3), 1)
    except Exception:
        pass

    # CPU from /proc/stat (snapshot — not delta, just presence)
    try:
        proc_stat = Path(f"/proc/{pid}/stat")
        if proc_stat.exists():
            fields = proc_stat.read_text().split()
            res["cpu_utime"] = int(fields[13])
            res["cpu_stime"] = int(fields[14])
            res["state"] = fields[2]  # R/S/D/Z/T
    except Exception:
        pass

    return res


def _get_queue_state() -> Dict[str, int]:
    """Get message queue counts by status."""
    out = {"pending": 0, "processing": 0, "done": 0, "error": 0}
    try:
        queue_db = os.path.join(_VAULT, "message_queue.db")
        if not os.path.exists(queue_db):
            return out
        conn = sqlite3.connect(queue_db, timeout=2)
        for status, n in conn.execute(
            "SELECT status, COUNT(*) FROM queue GROUP BY status"
        ).fetchall():
            out[str(status)] = int(n)
        conn.close()
    except Exception:
        pass
    return out


def _extract_errors(logs: List[str], n: int) -> List[str]:
    """Extract recent error lines from logs."""
    errors = [
        line.strip()[:150] for line in logs
        if "ERROR" in line or "CRITICAL" in line or "Exception" in line
    ]
    return errors[-n:]


# Indicadores de un PROBLEMA real en una línea de log. No basta con que
# aparezca el nombre del proveedor: el gateway SIEMPRE loggea "telegram",
# así que contar la mera mención producía un 'timeout_externo' permanente
# y falso. Sólo contamos un proveedor si su línea trae además uno de estos.
_PROBLEM_INDICATORS = (
    "timeout", "timed out", "connecttimeout", "readtimeout", "writetimeout",
    "connecterror", "connectionerror", "connection refused", "unreachable",
    "circuit open", "circuit_open", "circuit opened",
    "failed", "failure", "error", "exception",
    " 429", " 500", " 502", " 503", " 504",
)
_PROVIDER_PATTERNS = {
    "OpenAI": ("openai", "api.openai"),
    "Telegram": ("telegram", "api.telegram"),
    "eToro": ("etoro",),
    "Search": ("tavily", "jina"),
    "Gemini": ("gemini",),
}


def _detect_external_calls(logs: List[str]) -> Dict[str, Any]:
    """Detect external API calls that show REAL problem evidence.

    A provider is counted as 'pending'/problematic ONLY when a recent log
    line mentions it AND that same line carries a problem indicator
    (timeout, connection error, circuit open, 5xx/429, ...). The mere
    presence of a provider name is NOT evidence of a timeout — counting it
    produced a permanent, misleading 'timeout_externo' diagnosis.

    Providers merely seen (no error) are surfaced separately as 'seen_apis'
    for context, without being counted.
    """
    problem_apis = set()
    seen_apis = set()
    timeout_detected = False
    for line in logs[-50:]:
        lower = line.lower()
        has_problem = any(ind in lower for ind in _PROBLEM_INDICATORS)
        if "timeout" in lower or "timed out" in lower:
            timeout_detected = True
        for name, needles in _PROVIDER_PATTERNS.items():
            if any(n in lower for n in needles):
                seen_apis.add(name)
                if has_problem:
                    problem_apis.add(name)
    if timeout_detected:
        problem_apis.add("timeout_detected")
    return {
        "pending_apis": sorted(problem_apis),
        "pending_count": len(problem_apis),
        "seen_apis": sorted(seen_apis),
    }


def _extract_traceback(logs: List[str]) -> str:
    """Extract the last traceback from logs if present."""
    tb_lines = []
    in_tb = False
    for line in logs:
        if "Traceback (most recent" in line:
            in_tb = True
            tb_lines = [line]
        elif in_tb:
            tb_lines.append(line)
            if line.strip() and not line.startswith(" ") and not line.startswith("\t"):
                if "Error" in line or "Exception" in line:
                    break
    return "\n".join(tb_lines[-15:]) if tb_lines else ""


# ── Persistence ───────────────────────────────────────────────────────

def _save_incident(record: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(_INCIDENTS_FILE), exist_ok=True)
        with open(_INCIDENTS_FILE, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        # Prune old entries
        if os.path.exists(_INCIDENTS_FILE):
            lines = open(_INCIDENTS_FILE).readlines()
            if len(lines) > _MAX_INCIDENTS:
                with open(_INCIDENTS_FILE, "w") as f:
                    f.writelines(lines[-_MAX_INCIDENTS:])
    except Exception as exc:
        logger.debug("save_incident failed: %s", exc)


def get_incidents(limit: int = 20) -> List[Dict[str, Any]]:
    """Read recent incidents for dashboard display."""
    if not os.path.exists(_INCIDENTS_FILE):
        return []
    incidents = []
    try:
        with open(_INCIDENTS_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    incidents.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except Exception:
        pass
    return incidents[-limit:]


def submit_feedback(
    incident_id: str,
    verdict: str,
    creator_cause: str = "",
    notes: str = "",
) -> Dict[str, Any]:
    """Creator validates or rejects a BlackBox diagnosis.

    Args:
        incident_id: e.g. 'INC-1780693107-PIP'
        verdict: 'confirmed' | 'rejected' | 'partial'
        creator_cause: the real cause if different from BlackBox diagnosis
        notes: free-text notes from the creator

    The feedback is saved alongside the incident and used to improve
    future diagnosis accuracy.
    """
    feedback = {
        "incident_id": incident_id,
        "type": "feedback",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "verdict": verdict,
        "creator_cause": creator_cause,
        "notes": notes,
    }
    _save_incident(feedback)

    # Log to observation ledger
    try:
        from core.self_observation.observation_ledger import record
        icon = {"confirmed": "\u2705", "rejected": "\u274c", "partial": "\u26a0\ufe0f"}.get(verdict, "?")
        record(
            domain="operator",
            obs_type="diagnosis_feedback",
            summary=(
                f"{icon} Feedback {incident_id}: {verdict}"
                + (f" — causa real: {creator_cause}" if creator_cause else "")
                + (f" — {notes[:60]}" if notes else "")
            )[:200],
            evidence=feedback,
        )
    except Exception:
        pass

    logger.info(
        "BLACKBOX FEEDBACK | %s | verdict=%s | creator_cause=%s",
        incident_id, verdict, creator_cause or "(same)",
    )
    return feedback


def get_feedback_stats() -> Dict[str, Any]:
    """Aggregate feedback stats for diagnosis accuracy tracking."""
    incidents = get_incidents(limit=200)
    feedbacks = [i for i in incidents if i.get("type") == "feedback"]
    diagnoses = [i for i in incidents if "causa_probable" in i]
    confirmed = sum(1 for f in feedbacks if f.get("verdict") == "confirmed")
    rejected = sum(1 for f in feedbacks if f.get("verdict") == "rejected")
    partial = sum(1 for f in feedbacks if f.get("verdict") == "partial")
    total = confirmed + rejected + partial
    return {
        "total_diagnoses": len(diagnoses),
        "total_feedback": total,
        "confirmed": confirmed,
        "rejected": rejected,
        "partial": partial,
        "accuracy_pct": round(confirmed / max(total, 1) * 100, 1),
        "pending_review": len(diagnoses) - total,
    }


# ── Observation ledger + Telegram ─────────────────────────────────────

def _log_to_observation_ledger(incident: Dict) -> None:
    try:
        from core.self_observation.observation_ledger import record
        record(
            domain="operator",
            obs_type="worker_incident",
            summary=(
                f"Worker {incident['worker_name']} colgado (PID {incident['worker_pid']}, "
                f"HB {incident['heartbeat_age_s']:.0f}s) — snapshot capturado"
            )[:200],
            star_id=f"worker:{incident['worker_name']}",
            evidence={
                "incident_id": incident["incident_id"],
                "pid": incident["worker_pid"],
                "heartbeat_age": incident["heartbeat_age_s"],
                "active_task": incident.get("active_task", {}).get("msg_id", "none"),
                "queue_pending": incident.get("queue", {}).get("pending", 0),
                "ram_mb": incident.get("resources", {}).get("ram_mb", 0),
            },
            severity="warning",
        )
    except Exception:
        pass


def _log_diagnosis_to_ledger(diag: Dict) -> None:
    try:
        from core.self_observation.observation_ledger import record
        record(
            domain="operator",
            obs_type="worker_diagnosis",
            summary=(
                f"Diagnóstico {diag['incident_id']}: {diag['causa_probable']} "
                f"(confianza {diag['confianza']:.0%}) — {diag['accion_recomendada']}"
            )[:200],
            evidence={
                "incident_id": diag["incident_id"],
                "causa": diag["causa_probable"],
                "confianza": diag["confianza"],
                "solucion": diag.get("solucion_propuesta", "")[:100],
            },
            severity="warning" if diag["confianza"] >= 0.6 else "info",
        )
    except Exception:
        pass


def _alert_creator(incident: Dict, diag: Dict) -> None:
    """Send Telegram alert with incident + diagnosis to creator."""
    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CREATOR_CHAT_ID", "")
        if not token or not chat_id:
            return

        task = incident.get("active_task", {})
        res = incident.get("resources", {})
        q = incident.get("queue", {})
        pos = diag.get("evidencia_positiva", diag.get("evidencia", []))
        neg = diag.get("evidencia_negativa", [])

        msg = (
            f"🔴 <b>Worker Incident — {incident['incident_id']}</b>\n"
            f"<code>{incident['worker_name']}</code> PID {incident['worker_pid']}\n"
            f"HB stale: {incident['heartbeat_age_s']:.0f}s\n\n"
            f"📊 <b>Snapshot</b>\n"
            f"• RAM: {res.get('ram_mb', '?')}MB\n"
            f"• Cola: {q.get('pending', 0)} pend / {q.get('processing', 0)} proc\n"
            f"• Tarea: {task.get('msg_id', 'none')} ({task.get('duration_s', 0):.0f}s)\n\n"
            f"🔍 <b>Causa: {diag['causa_probable']}</b> ({diag['confianza']:.0%})\n\n"
            f"✅ <b>Evidencia positiva:</b>\n"
        )
        for e in pos[:3]:
            msg += f"  • {e[:80]}\n"
        if neg:
            msg += f"\n❌ <b>Evidencia negativa:</b>\n"
            for e in neg[:3]:
                msg += f"  • {e[:80]}\n"
        msg += (
            f"\n🔧 <b>Acción:</b> {diag['accion_recomendada']}\n"
            f"💡 <b>Solución:</b> {diag.get('solucion_propuesta', '')[:80]}"
        )

        import urllib.request
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps({
                "chat_id": chat_id, "text": msg, "parse_mode": "HTML",
            }).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as exc:
        logger.debug("blackbox alert failed: %s", exc)
