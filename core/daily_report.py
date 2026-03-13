"""
Vectrax — Reporte Diario de Estado
====================================
Genera un reporte diario consolidado del sistema:
  - Estado del sistema (daemon, versión)
  - Orquestador (queries, routing, divergencia)
  - Proveedores (latencia, errores, dominancia)
  - Autopatch (ciclos, fixes, rollbacks)
  - Ledger (eventos, facts)
  - Inbox (archivos procesados)
"""

import json
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime, date
from typing import Optional

BASE_DIR = os.path.expanduser("~/Vectrax")
ORCHESTRATOR_DB = os.path.join(BASE_DIR, "vectrax.db")
LEDGER_DB = os.path.join(BASE_DIR, "vault", "vectrax_ledger.db")
LOG_FILE = os.path.join(BASE_DIR, "logs", "autopatch.log")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
INBOX_DONE_DIR = os.path.join(BASE_DIR, "inbox_done")
PID_FILE = os.path.expanduser("~/.vectrax/vectrax.pid")

VERSION = "2.1.0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_connect(db_path):
    """Connect to a SQLite DB if it exists, else return None."""
    if not os.path.isfile(db_path):
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_query(conn, sql, params=()):
    """Run a query, return rows or empty list on error."""
    if conn is None:
        return []
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def _safe_scalar(conn, sql, params=(), default=0):
    """Run a scalar query, return value or default on error."""
    if conn is None:
        return default
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else default
    except sqlite3.OperationalError:
        return default


# ---------------------------------------------------------------------------
# Sección: Sistema
# ---------------------------------------------------------------------------

def _section_system():
    """Estado general del sistema."""
    info = {
        "version": VERSION,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "daemon_running": False,
        "daemon_pid": None,
    }

    if os.path.isfile(PID_FILE):
        try:
            with open(PID_FILE) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)  # check if alive
            info["daemon_running"] = True
            info["daemon_pid"] = pid
        except (ProcessLookupError, ValueError, PermissionError):
            info["daemon_running"] = False

    return info


# ---------------------------------------------------------------------------
# Sección: Orquestador (vectrax.db)
# ---------------------------------------------------------------------------

def _section_orchestrator(target_date: str):
    """Stats del orquestador para una fecha dada (YYYY-MM-DD)."""
    conn = _safe_connect(ORCHESTRATOR_DB)
    stats = {
        "db_exists": conn is not None,
        "queries_today": 0,
        "topics": [],
        "routing_modes": [],
        "avg_divergence": 0.0,
        "high_divergence_count": 0,
        "single_savings_pct": 0.0,
        "consensus_count": 0,
        "fallback_count": 0,
    }

    if conn is None:
        return stats

    date_filter = f"{target_date}%"

    # Queries del día
    stats["queries_today"] = _safe_scalar(
        conn,
        "SELECT COUNT(*) FROM queries WHERE created_at LIKE ?",
        (date_filter,)
    )

    if stats["queries_today"] == 0:
        # Try DATE() format for compatibility
        stats["queries_today"] = _safe_scalar(
            conn,
            "SELECT COUNT(*) FROM queries WHERE DATE(created_at) = ?",
            (target_date,)
        )

    # Tópicos del día
    rows = _safe_query(
        conn,
        "SELECT topic, COUNT(*) AS cnt FROM queries "
        "WHERE DATE(created_at) = ? GROUP BY topic ORDER BY cnt DESC",
        (target_date,)
    )
    stats["topics"] = [{"topic": r["topic"], "count": int(r["cnt"])} for r in rows]

    # Modos de routing del día
    rows = _safe_query(
        conn,
        "SELECT routing_mode, COUNT(*) AS cnt FROM queries "
        "WHERE DATE(created_at) = ? AND routing_mode != '' "
        "GROUP BY routing_mode ORDER BY cnt DESC",
        (target_date,)
    )
    total_routed = sum(int(r["cnt"]) for r in rows)
    stats["routing_modes"] = [
        {
            "mode": r["routing_mode"],
            "count": int(r["cnt"]),
            "pct": round(int(r["cnt"]) / total_routed * 100, 1) if total_routed else 0.0,
        }
        for r in rows
    ]

    # Divergencia promedio del día
    stats["avg_divergence"] = round(
        _safe_scalar(
            conn,
            "SELECT AVG(divergence) FROM queries WHERE DATE(created_at) = ?",
            (target_date,),
            default=0.0,
        ) or 0.0,
        4,
    )

    # Alta divergencia
    stats["high_divergence_count"] = _safe_scalar(
        conn,
        "SELECT COUNT(*) FROM queries WHERE DATE(created_at) = ? AND divergence > 0.4",
        (target_date,),
    )

    # Ahorro SINGLE
    single_count = _safe_scalar(
        conn,
        "SELECT COUNT(*) FROM queries "
        "WHERE DATE(created_at) = ? AND routing_mode IN ('SINGLE', 'FALLBACK_CHAIN')",
        (target_date,),
    )
    stats["single_savings_pct"] = (
        round(single_count / stats["queries_today"] * 100, 1)
        if stats["queries_today"] > 0 else 0.0
    )

    # Consenso
    stats["consensus_count"] = _safe_scalar(
        conn,
        "SELECT COUNT(*) FROM queries WHERE DATE(created_at) = ? AND consensus_mode = 1",
        (target_date,),
    )

    # Fallbacks
    stats["fallback_count"] = _safe_scalar(
        conn,
        "SELECT COUNT(*) FROM queries WHERE DATE(created_at) = ? AND fallback_used = 1",
        (target_date,),
    )

    conn.close()
    return stats


# ---------------------------------------------------------------------------
# Sección: Proveedores
# ---------------------------------------------------------------------------

def _section_providers(target_date: str):
    """Rendimiento de proveedores para una fecha dada."""
    conn = _safe_connect(ORCHESTRATOR_DB)
    stats = {
        "providers": [],
        "feedback_today": 0,
    }

    if conn is None:
        return stats

    # Latencia y errores por proveedor
    rows = _safe_query(
        conn,
        "SELECT r.provider, "
        "  COUNT(*) AS calls, "
        "  AVG(r.latency_ms) AS avg_ms, "
        "  SUM(CASE WHEN r.error != '' THEN 1 ELSE 0 END) AS errors "
        "FROM responses r "
        "JOIN queries q ON r.query_id = q.id "
        "WHERE DATE(q.created_at) = ? "
        "GROUP BY r.provider",
        (target_date,),
    )
    for r in rows:
        calls = int(r["calls"])
        errors = int(r["errors"])
        stats["providers"].append({
            "provider": r["provider"],
            "calls": calls,
            "avg_latency_ms": round(float(r["avg_ms"] or 0), 1),
            "errors": errors,
            "error_rate": round(errors / calls, 4) if calls else 0.0,
        })

    # Feedback del día
    stats["feedback_today"] = _safe_scalar(
        conn,
        "SELECT COUNT(*) FROM feedback WHERE DATE(created_at) = ?",
        (target_date,),
    )

    conn.close()
    return stats


# ---------------------------------------------------------------------------
# Sección: Autopatch (reutiliza lógica de reporter.py)
# ---------------------------------------------------------------------------

def _section_autopatch():
    """Parse autopatch.log y resume stats."""
    stats = {
        "total_cycles": 0,
        "fixes_applied": 0,
        "rollbacks": 0,
        "errors_detected": 0,
        "last_cycle_date": None,
        "recent_errors": [],
    }

    if not os.path.isfile(LOG_FILE):
        return stats

    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    ts_pattern = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")

    for line in lines:
        stripped = line.strip()

        if "AUTOPATCH CYCLE START" in stripped:
            stats["total_cycles"] += 1
            m = ts_pattern.match(stripped)
            if m:
                stats["last_cycle_date"] = m.group(1)

        if "FIXED" in stripped and "COMPLETE" in stripped:
            stats["fixes_applied"] += 1

        if "ROLLBACK" in stripped and "COMPLETE" in stripped:
            stats["rollbacks"] += 1

        if "[ERROR]" in stripped or "[FAIL]" in stripped:
            stats["errors_detected"] += 1
            msg = stripped.split("]", 2)[-1].strip() if "]" in stripped else stripped
            if len(stats["recent_errors"]) < 5:
                stats["recent_errors"].append(msg)

    # Último reporte JSON
    report_file = os.path.join(REPORTS_DIR, "autopatch_last.json")
    if os.path.isfile(report_file):
        try:
            with open(report_file) as f:
                stats["last_report"] = json.load(f)
        except (json.JSONDecodeError, OSError):
            stats["last_report"] = None
    else:
        stats["last_report"] = None

    return stats


# ---------------------------------------------------------------------------
# Sección: Ledger (vault/vectrax_ledger.db)
# ---------------------------------------------------------------------------

def _section_ledger():
    """Stats de la base de conocimiento."""
    conn = _safe_connect(LEDGER_DB)
    stats = {
        "db_exists": conn is not None,
        "events_count": 0,
        "facts_count": 0,
        "knowledge_count": 0,
        "last_event": None,
    }

    if conn is None:
        return stats

    stats["events_count"] = _safe_scalar(conn, "SELECT COUNT(*) FROM events")
    stats["facts_count"] = _safe_scalar(conn, "SELECT COUNT(*) FROM facts")
    stats["knowledge_count"] = _safe_scalar(conn, "SELECT COUNT(*) FROM knowledge")

    rows = _safe_query(conn, "SELECT timestamp FROM events ORDER BY id DESC LIMIT 1")
    if rows:
        stats["last_event"] = rows[0]["timestamp"]

    conn.close()
    return stats


# ---------------------------------------------------------------------------
# Sección: Inbox
# ---------------------------------------------------------------------------

def _section_inbox(target_date: str):
    """Archivos procesados (en inbox_done) para la fecha dada."""
    stats = {
        "processed_today": 0,
        "total_processed": 0,
    }

    if not os.path.isdir(INBOX_DONE_DIR):
        return stats

    date_compact = target_date.replace("-", "")  # YYYYMMDD

    try:
        files = os.listdir(INBOX_DONE_DIR)
    except OSError:
        return stats

    stats["total_processed"] = len(files)
    stats["processed_today"] = sum(1 for f in files if f.startswith(date_compact))

    return stats


# ---------------------------------------------------------------------------
# Reporte completo
# ---------------------------------------------------------------------------

def generate_report(target_date: str = None) -> dict:
    """Genera el reporte diario completo como dict."""
    if target_date is None:
        target_date = date.today().isoformat()

    report = {
        "date": target_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "system": _section_system(),
        "orchestrator": _section_orchestrator(target_date),
        "providers": _section_providers(target_date),
        "autopatch": _section_autopatch(),
        "ledger": _section_ledger(),
        "inbox": _section_inbox(target_date),
    }

    return report


def save_report(report: dict) -> str:
    """Guarda el reporte en reports/daily_YYYY-MM-DD.json. Retorna la ruta."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    filename = f"daily_{report['date']}.json"
    path = os.path.join(REPORTS_DIR, filename)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    return path


def load_saved_report(target_date: str) -> Optional[dict]:
    """Carga un reporte previamente guardado."""
    path = os.path.join(REPORTS_DIR, f"daily_{target_date}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Formato consola
# ---------------------------------------------------------------------------

def print_report(report: dict):
    """Imprime el reporte formateado a consola."""
    print()
    print(f"  ╔══════════════════════════════════════╗")
    print(f"  ║   VECTRAX — Reporte Diario de Estado ║")
    print(f"  ╚══════════════════════════════════════╝")
    print(f"  Fecha:     {report['date']}")
    print(f"  Generado:  {report['generated_at']}")

    # --- Sistema ---
    sys_info = report["system"]
    daemon_status = "activo" if sys_info["daemon_running"] else "inactivo"
    daemon_detail = f" (PID {sys_info['daemon_pid']})" if sys_info["daemon_pid"] else ""
    print()
    print(f"  ── Sistema ──")
    print(f"  Versión:   {sys_info['version']}")
    print(f"  Daemon:    {daemon_status}{daemon_detail}")

    # --- Orquestador ---
    orch = report["orchestrator"]
    print()
    print(f"  ── Orquestador ──")
    if not orch["db_exists"]:
        print(f"  Base de datos no encontrada.")
    else:
        print(f"  Queries hoy:         {orch['queries_today']}")
        print(f"  Divergencia prom:    {orch['avg_divergence']}")
        print(f"  Alta divergencia:    {orch['high_divergence_count']}")
        print(f"  Consenso activado:   {orch['consensus_count']}")
        print(f"  Fallbacks:           {orch['fallback_count']}")
        print(f"  Ahorro SINGLE:       {orch['single_savings_pct']}%")

        if orch["topics"]:
            print(f"  Tópicos:")
            for t in orch["topics"][:5]:
                print(f"    - {t['topic']}: {t['count']}")

        if orch["routing_modes"]:
            print(f"  Routing:")
            for m in orch["routing_modes"]:
                print(f"    - {m['mode']}: {m['count']} ({m['pct']}%)")

    # --- Proveedores ---
    prov = report["providers"]
    print()
    print(f"  ── Proveedores ──")
    if not prov["providers"]:
        print(f"  Sin actividad de proveedores hoy.")
    else:
        for p in prov["providers"]:
            err_indicator = f" ⚠ {p['errors']} errores" if p["errors"] > 0 else ""
            print(f"  {p['provider'].upper()}:")
            print(f"    Llamadas:    {p['calls']}")
            print(f"    Latencia:    {p['avg_latency_ms']} ms{err_indicator}")
            print(f"    Error rate:  {p['error_rate'] * 100:.1f}%")
    if prov["feedback_today"]:
        print(f"  Feedback recibido: {prov['feedback_today']}")

    # --- Autopatch ---
    ap = report["autopatch"]
    print()
    print(f"  ── Autopatch ──")
    print(f"  Ciclos totales:      {ap['total_cycles']}")
    print(f"  Fixes aplicados:     {ap['fixes_applied']}")
    print(f"  Rollbacks:           {ap['rollbacks']}")
    print(f"  Errores detectados:  {ap['errors_detected']}")
    if ap["last_cycle_date"]:
        print(f"  Último ciclo:        {ap['last_cycle_date']}")
    if ap.get("last_report"):
        result = ap["last_report"].get("result", "N/A")
        print(f"  Último resultado:    {result}")
    if ap["recent_errors"]:
        print(f"  Errores recientes:")
        for err in ap["recent_errors"][-3:]:
            print(f"    ! {err[:80]}")

    # --- Ledger ---
    ledger = report["ledger"]
    print()
    print(f"  ── Ledger ──")
    if not ledger["db_exists"]:
        print(f"  Base de datos no encontrada.")
    else:
        print(f"  Eventos:       {ledger['events_count']}")
        print(f"  Facts:         {ledger['facts_count']}")
        print(f"  Knowledge:     {ledger['knowledge_count']}")
        if ledger["last_event"]:
            print(f"  Último evento: {ledger['last_event']}")

    # --- Inbox ---
    inbox = report["inbox"]
    print()
    print(f"  ── Inbox ──")
    print(f"  Procesados hoy:  {inbox['processed_today']}")
    print(f"  Total histórico:  {inbox['total_processed']}")

    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    target = None
    json_only = False

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--json":
            json_only = True
        elif args[i] == "--date" and i + 1 < len(args):
            target = args[i + 1]
            i += 1
        i += 1

    # Si piden fecha y existe reporte guardado, usarlo
    if target:
        saved = load_saved_report(target)
        if saved and json_only:
            print(json.dumps(saved, indent=2, ensure_ascii=False))
            sys.exit(0)
        elif saved:
            print_report(saved)
            sys.exit(0)

    report = generate_report(target_date=target)
    save_path = save_report(report)

    if json_only:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_report(report)
        print(f"  Guardado en: {save_path}")
        print()
