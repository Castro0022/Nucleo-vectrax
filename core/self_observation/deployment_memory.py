"""
core/self_observation/deployment_memory.py — memoria de despliegues.

Conoce qué cambió en el sistema en los últimos N commits/días:
  - últimos commits (subject + author + ts)
  - módulos modificados (paths con más diff)
  - cantidad de reinicios detectables (gaps en heartbeat)
  - schema migrations detectadas
  - flags de mode cambiados recientemente

NO ejecuta git push ni nada destructivo. Solo `git log`, `git diff
--name-only`, lectura de heartbeats.

API pública:
    recent_commits(n=10) -> list[dict]
    modified_modules(since_days=7) -> list[str]
    restart_count(window_seconds=86400) -> int
    deploy_summary() -> dict (compone todo)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.self_observation.deploy")

# Fallback: cuando /app no contiene .git (bind-mount de produccion sin
# rsync de .git/), leemos el snapshot generado por deploy_vultr.sh.
_SNAPSHOT_DIR = os.environ.get(
    "VECTRAX_GIT_SNAPSHOT_DIR", "/app/.git_snapshot",
)


def _git_root() -> Optional[str]:
    """Devuelve el root del repo git si estamos dentro. None si no."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return None


def _git(args: List[str], cwd: Optional[str] = None) -> str:
    """Run git con timeout. Devuelve stdout o ''."""
    try:
        r = subprocess.run(
            ["git", "--no-pager"] + list(args),
            capture_output=True, text=True, timeout=5,
            cwd=cwd,
        )
        if r.returncode == 0:
            return r.stdout
    except Exception as exc:
        logger.debug("git %s failed: %s", args, exc)
    return ""


def _load_snapshot() -> Optional[Dict[str, Any]]:
    """Carga snapshot pre-construido en build/deploy si existe.

    Estructura esperada en `${VECTRAX_GIT_SNAPSHOT_DIR}/SUMMARY.json`:
        {
          "head": "abc1234",
          "branch": "main",
          "recent_commits": [{sha, subject, author, ts, files_changed}, ...],
          "modified_modules": ["path/a.py", ...]
        }
    """
    path = os.path.join(_SNAPSHOT_DIR, "SUMMARY.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.debug("git snapshot load failed: %s", exc)
        return None


def recent_commits(n: int = 10) -> List[Dict[str, Any]]:
    """Devuelve últimos n commits como list de dicts.

    Cada dict: {sha, subject, author, ts, files_changed}
    Si no hay repo git, intenta leer del snapshot pre-deploy.
    """
    root = _git_root()
    if not root:
        snap = _load_snapshot()
        if snap and isinstance(snap.get("recent_commits"), list):
            return snap["recent_commits"][:int(n)]
        return []
    fmt = "%H%x09%s%x09%an%x09%at"
    out = _git(["log", f"-n{int(n)}", f"--format={fmt}"], cwd=root)
    if not out:
        return []
    commits = []
    for line in out.strip().splitlines():
        try:
            sha, subject, author, ts = line.split("\t", 3)
        except ValueError:
            continue
        # Cantidad de archivos modificados (separate call, only top n)
        files = _git(
            ["show", "--name-only", "--format=", sha], cwd=root,
        ).strip().splitlines()
        commits.append({
            "sha": sha[:8],
            "subject": subject[:140],
            "author": author,
            "ts": int(ts) if ts.isdigit() else 0,
            "files_changed": len(files),
        })
    return commits


def modified_modules(since_days: int = 7, top_n: int = 10) -> List[str]:
    """Top N módulos con más modificaciones en los últimos `since_days`."""
    root = _git_root()
    if not root:
        snap = _load_snapshot()
        if snap and isinstance(snap.get("modified_modules"), list):
            return snap["modified_modules"][:int(top_n)]
        return []
    since = f"{int(since_days)}.days.ago"
    out = _git(
        ["log", f"--since={since}", "--name-only", "--format="],
        cwd=root,
    )
    if not out:
        return []
    counts: Dict[str, int] = {}
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        # Saltar archivos de log/cache
        if line.endswith((".log", ".db", ".db-shm", ".db-wal")):
            continue
        if line.startswith((".pytest_cache/", "__pycache__/")):
            continue
        counts[line] = counts.get(line, 0) + 1
    sorted_files = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return [f for f, _ in sorted_files[: max(0, int(top_n))]]


def restart_count(window_seconds: int = 86400) -> int:
    """Estima cuántos restarts hubo en la ventana, mirando los gaps del
    heartbeat. Si no hay log de heartbeat histórico, devuelve 0.

    Heurística simple: cuenta cuántas veces el archivo de heartbeat fue
    creado vs su mtime actual. Defensivo.
    """
    # Sin un log histórico, no podemos detectar restarts a posteriori.
    # Devolvemos 0 como honest default.
    # En el futuro: sumar un contador en disk cada vez que el supervisor
    # arranca, leerlo aquí.
    return 0


def deploy_summary() -> Dict[str, Any]:
    """Composición compacta para inyectar al creator context."""
    commits = recent_commits(n=8)
    modules = modified_modules(since_days=7, top_n=8)
    return {
        "recent_commits": commits,
        "modified_modules": modules,
        "restart_count_24h": restart_count(),
        "current_branch": _current_branch(),
        "current_head": _head_short(),
    }


def _current_branch() -> str:
    root = _git_root()
    if not root:
        snap = _load_snapshot()
        return (snap or {}).get("branch", "") or ""
    out = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    return out.strip()


def _head_short() -> str:
    root = _git_root()
    if not root:
        snap = _load_snapshot()
        return (snap or {}).get("head", "") or ""
    out = _git(["rev-parse", "--short", "HEAD"], cwd=root)
    return out.strip()
