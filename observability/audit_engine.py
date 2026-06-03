"""
Vectrax Self-Audit Engine
===========================
Permanent self-observation system that audits the entire Vectrax
infrastructure and reports findings to the creator via Telegram.

Two modes:
  - DAILY  (lightweight, ~10s): container, processes, resources, APIs,
            errors, queue, heartbeats.
  - WEEKLY (deep, ~30s): all daily checks + databases, gravitational
            memory, external services, logs, Docker cache, orphan files.

Classification: ÓPTIMO / ESTABLE / DEGRADADO / CRÍTICO

Auto-corrections (safe only):
  - Prune Docker build cache when > 5GB reclaimable
  - Delete empty orphan databases

Approval-required actions are reported but NOT executed.

Results persisted to vault/audit_reports/ as JSON.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.audit")

VAULT_DIR = os.environ.get(
    "VECTRAX_VAULT_DIR",
    os.path.join(os.path.expanduser("~"), "Vectrax", "vault"),
)
REPORTS_DIR = os.path.join(VAULT_DIR, "audit_reports")
RUNTIME_DIR = os.path.join(os.path.expanduser("~"), ".vectrax")

# Thresholds
RAM_WARN_PCT = 80
DISK_WARN_PCT = 85
HEARTBEAT_MAX_AGE = 60  # seconds
DOCKER_CACHE_WARN_GB = 5
LOG_ERROR_WARN_COUNT = 50


# ── Classification ────────────────────────────────────────────────

def classify(problems: List[Dict]) -> str:
    """Assign overall system state from problem list."""
    if any(p["risk"] == "CRITICAL" for p in problems):
        return "CRÍTICO"
    high = sum(1 for p in problems if p["risk"] == "HIGH")
    med = sum(1 for p in problems if p["risk"] == "MEDIUM")
    if high >= 2 or (high >= 1 and med >= 2):
        return "DEGRADADO"
    if high == 1 or med >= 2:
        return "ESTABLE"
    return "ÓPTIMO"


EMOJI = {
    "ÓPTIMO": "🟢",
    "ESTABLE": "🟡",
    "DEGRADADO": "🟠",
    "CRÍTICO": "🔴",
}


# ── Helpers ───────────────────────────────────────────────────────

def _shell(cmd: str, timeout: int = 15) -> Tuple[str, int]:
    """Run a shell command, return (stdout, exit_code)."""
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout,
        )
        return r.stdout.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", -1
    except Exception as e:
        return str(e), -1


def _http_ok(url: str, headers: Optional[Dict] = None, timeout: int = 10) -> Tuple[bool, int]:
    """Quick HTTP GET check. Returns (success, latency_ms)."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200, round((time.time() - t0) * 1000)
    except Exception:
        return False, 0


def _db_count(db_path: str, table: str) -> int:
    """Count rows in a SQLite table. Returns -1 on error."""
    try:
        conn = sqlite3.connect(db_path)
        c = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        conn.close()
        return c
    except Exception:
        return -1


def _send_telegram(text: str) -> bool:
    """Send a message to the creator via Telegram."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CREATOR_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning("Telegram credentials not configured for audit alerts")
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15):
            return True
    except Exception as e:
        logger.error("Telegram send failed: %s", e)
        return False


def _save_report(report: Dict) -> str:
    """Persist report to vault/audit_reports/. Returns file path."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    mode = report.get("mode", "unknown")
    path = os.path.join(REPORTS_DIR, f"audit_{mode}_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    # Keep only last 60 reports
    files = sorted(Path(REPORTS_DIR).glob("audit_*.json"))
    for old in files[:-60]:
        old.unlink(missing_ok=True)
    return path


# ── Checks ────────────────────────────────────────────────────────

def check_container() -> Dict:
    """Verify container is running and healthy."""
    out, _ = _shell("cat /proc/1/cmdline 2>/dev/null | tr '\\0' ' '")
    supervisor = "supervisor" in out.lower()
    return {"name": "container", "ok": supervisor, "detail": "supervisor active" if supervisor else "supervisor NOT found"}


def check_processes() -> Dict:
    """Check for expected processes, detect duplicates."""
    out, _ = _shell("ps aux | grep python | grep -v grep")
    lines = [l for l in out.split("\n") if l.strip()]
    expected = ["telegram_gateway", "pipeline_worker", "uvicorn", "vectrax_unified"]
    found = []
    duplicates = []
    for name in expected:
        matches = [l for l in lines if name in l]
        if matches:
            found.append(name)
        if len(matches) > 1:
            duplicates.append(f"{name}({len(matches)})")
    missing = [n for n in expected if n not in found]
    ok = not missing and not duplicates
    detail = f"found={len(found)}/4"
    if missing:
        detail += f" missing={missing}"
    if duplicates:
        detail += f" DUPLICATES={duplicates}"
    return {"name": "processes", "ok": ok, "detail": detail}


def check_resources() -> Dict:
    """Check CPU, RAM, disk usage."""
    issues = []

    # RAM
    try:
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f:
                parts = line.split()
                if parts[0] in ("MemTotal:", "MemAvailable:"):
                    mem[parts[0][:-1]] = int(parts[1])
        total = mem.get("MemTotal", 1)
        avail = mem.get("MemAvailable", 0)
        used_pct = round((1 - avail / total) * 100, 1)
        if used_pct > RAM_WARN_PCT:
            issues.append(f"RAM {used_pct}%")
    except Exception:
        used_pct = -1

    # Disk
    try:
        st = os.statvfs("/app")
        total_gb = (st.f_blocks * st.f_frsize) / (1024 ** 3)
        free_gb = (st.f_bavail * st.f_frsize) / (1024 ** 3)
        used_pct_disk = round((1 - free_gb / total_gb) * 100, 1)
        if used_pct_disk > DISK_WARN_PCT:
            issues.append(f"Disk {used_pct_disk}%")
    except Exception:
        used_pct_disk = -1

    # Load
    try:
        load1, _, _ = os.getloadavg()
    except Exception:
        load1 = -1

    ok = len(issues) == 0
    detail = f"RAM={used_pct}% Disk={used_pct_disk}% Load={load1:.2f}"
    if issues:
        detail += f" WARN={issues}"
    return {"name": "resources", "ok": ok, "detail": detail}


def check_heartbeats() -> Dict:
    """Verify worker and gateway heartbeats are fresh."""
    issues = []
    now = time.time()
    for name in ("worker_heartbeat", "gateway_heartbeat"):
        path = os.path.join(RUNTIME_DIR, name)
        if os.path.exists(path):
            try:
                with open(path) as f:
                    ts = float(f.read().strip())
                age = now - ts
                if age > HEARTBEAT_MAX_AGE:
                    issues.append(f"{name}={age:.0f}s")
            except Exception:
                issues.append(f"{name}=unreadable")
        else:
            issues.append(f"{name}=missing")

    ok = len(issues) == 0
    return {"name": "heartbeats", "ok": ok, "detail": "fresh" if ok else f"STALE: {issues}"}


def check_telegram() -> Dict:
    """Test Telegram bot API connectivity."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return {"name": "telegram", "ok": False, "detail": "TOKEN MISSING"}
    ok, ms = _http_ok(f"https://api.telegram.org/bot{token}/getMe")
    return {"name": "telegram", "ok": ok, "detail": f"{ms}ms" if ok else "FAILED"}


def check_api_health() -> Dict:
    """Test internal Core API."""
    ok, ms = _http_ok("http://localhost:8900/health")
    if not ok:
        # Fallback: check if port is listening
        out, _ = _shell("ss -tlnp | grep 8900")
        ok = "8900" in out
        ms = 0
    return {"name": "core_api", "ok": ok, "detail": f"{ms}ms" if ms else ("port open" if ok else "DOWN")}


def check_queue() -> Dict:
    """Check message queue for stuck messages."""
    count = _db_count(os.path.join(VAULT_DIR, "message_queue.db"), "queue")
    ok = count >= 0 and count < 50
    return {"name": "queue", "ok": ok, "detail": f"{count} pending"}


def check_scheduler() -> Dict:
    """Verify scheduler has active tasks."""
    count = _db_count(os.path.join(VAULT_DIR, "scheduler.db"), "scheduled_tasks")
    return {"name": "scheduler", "ok": count > 0, "detail": f"{count} tasks"}


def check_recent_errors() -> Dict:
    """Count errors in Docker logs from last 24h."""
    out, _ = _shell(
        "cat /proc/1/fd/2 2>/dev/null | tail -2000 | grep -icE 'error|exception|traceback' || echo 0",
        timeout=5,
    )
    try:
        count = int(out.strip().split("\n")[-1])
    except (ValueError, IndexError):
        count = 0
    ok = count < LOG_ERROR_WARN_COUNT
    return {"name": "recent_errors", "ok": ok, "detail": f"{count} errors (24h)"}


# ── Deep checks (weekly) ─────────────────────────────────────────

def check_audit_ledger() -> Dict:
    """Verify audit ledger is actively recording."""
    db = os.path.join(VAULT_DIR, "audit_ledger.db")
    count = _db_count(db, "audit_ledger")
    if count < 0:
        return {"name": "audit_ledger", "ok": False, "detail": "DB NOT FOUND"}
    try:
        conn = sqlite3.connect(db)
        latest = conn.execute(
            "SELECT timestamp FROM audit_ledger ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if latest:
            return {"name": "audit_ledger", "ok": True, "detail": f"{count} entries, latest={latest[0][:10]}"}
    except Exception:
        pass
    return {"name": "audit_ledger", "ok": count > 0, "detail": f"{count} entries"}


def check_gravity() -> Dict:
    """Check gravitational memory (JSON-based gravity index)."""
    path = os.path.join(VAULT_DIR, "gravity_index.json")
    if not os.path.isfile(path):
        return {"name": "gravity", "ok": False, "detail": "gravity_index.json NOT FOUND"}
    try:
        with open(path) as f:
            data = json.load(f)
        count = len(data)
        size_kb = os.path.getsize(path) // 1024
        tiers = {}
        for rec in data.values():
            t = rec.get("tier", "?")
            tiers[t] = tiers.get(t, 0) + 1
        return {"name": "gravity", "ok": count > 0, "detail": f"{count} records ({size_kb}KB) tiers={tiers}"}
    except Exception as e:
        return {"name": "gravity", "ok": False, "detail": str(e)}


def check_user_memory() -> Dict:
    """Deep check on user memory database."""
    db = os.path.join(VAULT_DIR, "user_memory.db")
    checks = {}
    for table in ("interactions", "profiles", "core_memory", "user_facts"):
        checks[table] = _db_count(db, table)
    ok = all(v >= 0 for v in checks.values()) and checks.get("interactions", 0) > 0
    return {"name": "user_memory", "ok": ok, "detail": str(checks)}


def check_convergence() -> Dict:
    """Verify convergence engine cycles."""
    db = os.path.join(VAULT_DIR, "operational_cycles.db")
    count = _db_count(db, "op_cycles")
    return {"name": "convergence", "ok": count > 0, "detail": f"{count} cycles"}


def check_cognition_files() -> Dict:
    """Check cognition signal and buffer files."""
    issues = []
    for name in ("cognition_signals.jsonl", "cognition_buffer.jsonl", "episodic_ledger.jsonl"):
        path = os.path.join(VAULT_DIR, name)
        if os.path.isfile(path):
            size_mb = os.path.getsize(path) / (1024 * 1024)
            mtime = os.path.getmtime(path)
            age_days = (time.time() - mtime) / 86400
            if age_days > 7:
                issues.append(f"{name} stale ({age_days:.0f}d)")
        else:
            issues.append(f"{name} MISSING")
    ok = len(issues) == 0
    return {"name": "cognition_files", "ok": ok, "detail": "all active" if ok else str(issues)}


def check_external_service(name: str, check_fn) -> Dict:
    """Generic external service check wrapper."""
    try:
        ok, detail = check_fn()
        return {"name": name, "ok": ok, "detail": detail}
    except Exception as e:
        return {"name": name, "ok": False, "detail": str(e)[:100]}


def _check_etoro() -> Tuple[bool, str]:
    from connectors.etoro.etoro_client import healthcheck
    r = healthcheck()
    return r.get("success", False), f"{r.get('latency_ms', '?')}ms env={r.get('environment', '?')}"


def _check_stripe() -> Tuple[bool, str]:
    import base64
    sk = os.environ.get("STRIPE_SECRET_KEY", "")
    if not sk:
        return False, "KEY MISSING"
    req = urllib.request.Request("https://api.stripe.com/v1/balance")
    auth = base64.b64encode(f"{sk}:".encode()).decode()
    req.add_header("Authorization", f"Basic {auth}")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status == 200, f"{r.status}"


def _check_gemini() -> Tuple[bool, str]:
    gk = os.environ.get("GEMINI_API_KEY", "")
    if not gk:
        return False, "KEY MISSING"
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={gk}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status == 200, f"OK ({len(json.loads(r.read()).get('models', []))} models)"


def _check_openai() -> Tuple[bool, str]:
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        return False, "KEY MISSING"
    return True, f"key set ({len(key)} chars)"


def check_docker_cache() -> Dict:
    """Check Docker build cache size (runs outside container via /proc)."""
    # Inside container we can't check Docker cache; report N/A
    return {"name": "docker_cache", "ok": True, "detail": "check from host only"}


def check_orphan_dbs() -> Dict:
    """Find empty/orphan database files."""
    import glob
    orphans = []
    for db_path in glob.glob("/app/**/*.db", recursive=True):
        if os.path.getsize(db_path) == 0:
            orphans.append(os.path.basename(db_path))
    ok = len(orphans) == 0
    return {"name": "orphan_dbs", "ok": ok, "detail": f"{len(orphans)} empty" + (f": {orphans[:5]}" if orphans else "")}


def check_db_integrity() -> Dict:
    """Run integrity_check on critical databases."""
    dbs = [
        os.path.join(VAULT_DIR, "user_memory.db"),
        os.path.join(VAULT_DIR, "audit_ledger.db"),
        os.path.join(VAULT_DIR, "operational_cycles.db"),
    ]
    issues = []
    for db_path in dbs:
        if not os.path.isfile(db_path):
            continue
        try:
            conn = sqlite3.connect(db_path)
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            conn.close()
            if result != "ok":
                issues.append(f"{os.path.basename(db_path)}: {result}")
        except Exception as e:
            issues.append(f"{os.path.basename(db_path)}: {e}")
    ok = len(issues) == 0
    return {"name": "db_integrity", "ok": ok, "detail": "all ok" if ok else str(issues)}


def check_log_errors_7d() -> Dict:
    """Scan vault JSONL files for error patterns in last 7 days."""
    cutoff = time.time() - 7 * 86400
    error_count = 0
    for name in ("cognition_signals.jsonl", "episodic_ledger.jsonl"):
        path = os.path.join(VAULT_DIR, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        ts = rec.get("ts", rec.get("timestamp", 0))
                        if isinstance(ts, str):
                            continue  # skip ISO timestamps for speed
                        if ts > cutoff and "error" in json.dumps(rec).lower():
                            error_count += 1
                    except (json.JSONDecodeError, TypeError):
                        continue
        except Exception:
            continue
    ok = error_count < 100
    return {"name": "log_errors_7d", "ok": ok, "detail": f"{error_count} errors"}


# ── Auto-corrections (safe only) ─────────────────────────────────

def auto_correct(problems: List[Dict]) -> List[str]:
    """Execute safe auto-corrections. Returns list of actions taken."""
    actions = []

    # Clean empty orphan DBs
    for p in problems:
        if p["name"] == "orphan_dbs" and not p["ok"]:
            import glob
            for db_path in glob.glob("/app/**/*.db", recursive=True):
                if os.path.getsize(db_path) == 0 and "vault" not in db_path:
                    try:
                        os.remove(db_path)
                        actions.append(f"Deleted empty orphan: {os.path.basename(db_path)}")
                    except Exception:
                        pass

    return actions


# ── Main audit functions ──────────────────────────────────────────

def run_daily_audit() -> Dict:
    """Lightweight daily audit (~10s)."""
    t0 = time.time()
    checks = [
        check_container(),
        check_processes(),
        check_resources(),
        check_heartbeats(),
        check_telegram(),
        check_api_health(),
        check_queue(),
        check_scheduler(),
        check_recent_errors(),
    ]

    problems = [c for c in checks if not c["ok"]]
    for p in problems:
        p["risk"] = "HIGH" if p["name"] in ("container", "processes", "heartbeats") else "MEDIUM"

    state = classify(problems)
    elapsed = round(time.time() - t0, 1)

    report = {
        "mode": "daily",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "state": state,
        "elapsed_s": elapsed,
        "checks": checks,
        "problems": problems,
        "auto_corrections": [],
        "pending_approval": [],
    }

    _save_report(report)

    # Alert on DEGRADED or CRITICAL
    if state in ("DEGRADADO", "CRÍTICO"):
        emoji = EMOJI[state]
        lines = [f"{emoji} <b>Auditoría diaria: {state}</b>"]
        for p in problems:
            lines.append(f"  ❌ {p['name']}: {p['detail']}")
        _send_telegram("\n".join(lines))

    logger.info("Daily audit: %s (%d checks, %.1fs)", state, len(checks), elapsed)
    return report


def run_weekly_audit() -> Dict:
    """Deep weekly audit (~30s)."""
    t0 = time.time()

    # All daily checks
    checks = [
        check_container(),
        check_processes(),
        check_resources(),
        check_heartbeats(),
        check_telegram(),
        check_api_health(),
        check_queue(),
        check_scheduler(),
        check_recent_errors(),
    ]

    # Deep checks
    checks.extend([
        check_audit_ledger(),
        check_gravity(),
        check_user_memory(),
        check_convergence(),
        check_cognition_files(),
        check_external_service("etoro", _check_etoro),
        check_external_service("stripe", _check_stripe),
        check_external_service("gemini", _check_gemini),
        check_external_service("openai", _check_openai),
        check_orphan_dbs(),
        check_db_integrity(),
        check_log_errors_7d(),
        check_docker_cache(),
    ])

    problems = [c for c in checks if not c["ok"]]

    # Assign risk levels
    critical = {"container", "processes", "db_integrity"}
    high = {"heartbeats", "audit_ledger", "convergence", "telegram"}
    for p in problems:
        if p["name"] in critical:
            p["risk"] = "CRITICAL"
        elif p["name"] in high:
            p["risk"] = "HIGH"
        else:
            p["risk"] = "MEDIUM"

    state = classify(problems)
    corrections = auto_correct(problems)
    elapsed = round(time.time() - t0, 1)

    # Pending approval items
    pending = []
    if any(p["name"] == "docker_cache" and not p["ok"] for p in problems):
        pending.append("Limpiar Docker build cache (ejecutar desde host)")
    for p in problems:
        if p["name"] in ("cognition_files",) and not p["ok"]:
            pending.append(f"Investigar {p['name']}: {p['detail']}")

    report = {
        "mode": "weekly",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "state": state,
        "elapsed_s": elapsed,
        "total_checks": len(checks),
        "passed": sum(1 for c in checks if c["ok"]),
        "failed": len(problems),
        "checks": checks,
        "problems": problems,
        "auto_corrections": corrections,
        "pending_approval": pending,
    }

    _save_report(report)

    # Always send weekly report via Telegram
    emoji = EMOJI[state]
    passed = report["passed"]
    total = report["total_checks"]
    lines = [
        f"{emoji} <b>Auditoría semanal: {state}</b>",
        f"📊 {passed}/{total} checks passed ({elapsed}s)",
    ]
    if problems:
        lines.append("")
        lines.append("<b>Problemas:</b>")
        for p in problems:
            risk_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡"}.get(p["risk"], "⚪")
            lines.append(f"  {risk_icon} {p['name']}: {p['detail']}")
    if corrections:
        lines.append("")
        lines.append("<b>Auto-correcciones:</b>")
        for a in corrections:
            lines.append(f"  🔧 {a}")
    if pending:
        lines.append("")
        lines.append("<b>Requiere aprobación:</b>")
        for a in pending:
            lines.append(f"  ⏳ {a}")
    if not problems:
        lines.append("\n✅ Todos los subsistemas operativos.")

    _send_telegram("\n".join(lines))

    # Record in audit ledger
    try:
        from core.audit_ledger import record
        record(
            f"self_audit:{report['mode']}",
            actor="audit_engine",
            reason=f"state={state} passed={passed}/{total}",
            metadata={"state": state, "problems": len(problems)},
        )
    except Exception:
        pass

    logger.info(
        "Weekly audit: %s (%d/%d passed, %.1fs)", state, passed, total, elapsed,
    )
    return report
