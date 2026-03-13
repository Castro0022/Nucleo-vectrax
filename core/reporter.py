"""
Vectrax Daily Status Reporter
==============================
Reads autopatch logs, latest report JSON, and DB state.
Produces a structured summary for `vx status`.
"""

import json
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime

BASE_DIR = os.path.expanduser("~/Vectrax")
LOG_FILE = os.path.join(BASE_DIR, "logs", "autopatch.log")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
DB_PATH = os.path.join(BASE_DIR, "vault", "vectrax_ledger.db")


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------

def parse_log_summary():
    """Parse autopatch.log and return aggregate stats."""
    stats = {
        "total_cycles": 0,
        "fixes_applied": 0,
        "rollbacks": 0,
        "errors_detected": 0,
        "last_cycle_date": None,
        "recent_errors": [],
        "recent_fixes": [],
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
            # Keep last 5 errors
            msg = stripped.split("]", 2)[-1].strip() if "]" in stripped else stripped
            if len(stats["recent_errors"]) < 5:
                stats["recent_errors"].append(msg)

        if "Fix:" in stripped:
            msg = stripped.split("Fix:", 1)[-1].strip()
            if len(stats["recent_fixes"]) < 5:
                stats["recent_fixes"].append(msg)

    return stats


# ---------------------------------------------------------------------------
# Report parsing
# ---------------------------------------------------------------------------

def load_latest_report():
    """Load the most recent JSON report from reports/."""
    if not os.path.isdir(REPORTS_DIR):
        return None

    json_files = sorted(
        [f for f in os.listdir(REPORTS_DIR) if f.endswith(".json")],
        key=lambda f: os.path.getmtime(os.path.join(REPORTS_DIR, f)),
        reverse=True,
    )

    if not json_files:
        return None

    try:
        with open(os.path.join(REPORTS_DIR, json_files[0]), "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return None


# ---------------------------------------------------------------------------
# DB status
# ---------------------------------------------------------------------------

def get_db_status():
    """Read basic stats from vectrax_ledger.db."""
    info = {
        "exists": False,
        "size_kb": 0,
        "events_count": 0,
        "facts_count": 0,
        "last_event": None,
    }

    if not os.path.isfile(DB_PATH):
        return info

    info["exists"] = True
    info["size_kb"] = round(os.path.getsize(DB_PATH) / 1024, 1)

    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        # Events count
        try:
            cur.execute("SELECT COUNT(*) FROM events")
            info["events_count"] = cur.fetchone()[0]
        except sqlite3.OperationalError:
            pass

        # Facts count
        try:
            cur.execute("SELECT COUNT(*) FROM facts")
            info["facts_count"] = cur.fetchone()[0]
        except sqlite3.OperationalError:
            pass

        # Last event timestamp
        try:
            cur.execute("SELECT timestamp FROM events ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            if row:
                info["last_event"] = row[0]
        except sqlite3.OperationalError:
            pass

        conn.close()
    except Exception:
        pass

    return info


# ---------------------------------------------------------------------------
# Summary builder
# ---------------------------------------------------------------------------

RESULT_ICONS = {
    "clean": "●",
    "fixed": "●",
    "rollback": "●",
    "failed": "●",
}

RESULT_LABELS = {
    "clean": "Limpio",
    "fixed": "Parcheado",
    "rollback": "Rollback",
    "failed": "Fallido",
}


def build_summary():
    """Build complete status summary dict."""
    log_stats = parse_log_summary()
    report = load_latest_report()
    db_info = get_db_status()

    return {
        "log": log_stats,
        "report": report,
        "db": db_info,
    }


def print_summary():
    """Print a visual status summary to stdout."""
    summary = build_summary()
    log = summary["log"]
    report = summary["report"]
    db = summary["db"]

    print()
    print("  --- Reporte de Estado ---")

    # Autopatch history
    print(f"  Ciclos totales:     {log['total_cycles']}")
    print(f"  Parches exitosos:   {log['fixes_applied']}")
    print(f"  Rollbacks:          {log['rollbacks']}")
    print(f"  Errores detectados: {log['errors_detected']}")
    if log["last_cycle_date"]:
        print(f"  Ultimo ciclo:       {log['last_cycle_date']}")

    # Last report
    if report:
        result = report.get("result", "N/A")
        icon = RESULT_ICONS.get(result, "?")
        label = RESULT_LABELS.get(result, result)
        print(f"  Ultimo resultado:   {icon} {label}")

        attempts = report.get("attempts", [])
        if attempts:
            total_errors = sum(len(a.get("errors_found", [])) for a in attempts)
            total_fixes = sum(len(a.get("fixes_applied", [])) for a in attempts)
            print(f"  Intentos:           {len(attempts)} (errores={total_errors}, fixes={total_fixes})")

            # Show error files from last report
            error_files = Counter()
            for a in attempts:
                for e in a.get("errors_found", []):
                    error_files[e.get("file", "?")] += 1
            if error_files:
                print("  Archivos con error:")
                for f, count in error_files.most_common(5):
                    print(f"    - {f} (x{count})")

            # Show successful fixes
            fix_files = set()
            for a in attempts:
                for fx in a.get("fixes_applied", []):
                    fix_files.add(fx)
            if fix_files:
                print("  Parches aplicados:")
                for fx in sorted(fix_files):
                    print(f"    + {fx}")
    else:
        print("  Ultimo resultado:   sin reportes")

    # Recent errors from log
    if log["recent_errors"]:
        print("  Errores recientes:")
        for err in log["recent_errors"][-3:]:
            print(f"    ! {err[:80]}")

    # DB status
    print(f"  Base de datos:      {'activa' if db['exists'] else 'no encontrada'}", end="")
    if db["exists"]:
        print(f" ({db['size_kb']} KB)")
        print(f"  Eventos:            {db['events_count']}")
        print(f"  Facts:              {db['facts_count']}")
        if db["last_event"]:
            print(f"  Ultimo evento:      {db['last_event']}")
    else:
        print()

    print()


if __name__ == "__main__":
    print_summary()
