#!/usr/bin/env python3
"""
scripts/build_git_snapshot.py — genera .git_snapshot/SUMMARY.json
==================================================================
Snapshot mínimo de estado git para que core/self_observation/
deployment_memory.py funcione en producción cuando /app no contiene
el directorio .git/ (caso típico cuando deploy_vultr.sh excluye .git
o el bind-mount oculta el snapshot horneado en imagen).

Uso:
    python scripts/build_git_snapshot.py

Salida: .git_snapshot/SUMMARY.json en el repo local.
Idempotente — sobrescribe el archivo cada vez.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path


def git(*args: str) -> str:
    r = subprocess.run(
        ["git", "--no-pager", *args],
        capture_output=True, text=True, timeout=10,
    )
    return r.stdout.strip() if r.returncode == 0 else ""


def main() -> int:
    head = git("rev-parse", "--short", "HEAD")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if not head:
        print("WARN: no git repo found — snapshot will be empty")

    fmt = "%H%x09%s%x09%an%x09%at"
    commits = []
    for line in git("log", "-n8", f"--format={fmt}").splitlines():
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        sha, subject, author, ts = parts
        files = [
            f for f in git("show", "--name-only", "--format=", sha).splitlines()
            if f.strip()
        ]
        commits.append({
            "sha": sha[:8],
            "subject": subject[:140],
            "author": author,
            "ts": int(ts) if ts.isdigit() else 0,
            "files_changed": len(files),
        })

    # Top modules in last 7 days
    counts: dict[str, int] = {}
    for line in git("log", "--since=7.days.ago", "--name-only", "--format=").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.endswith((".log", ".db", ".db-shm", ".db-wal")):
            continue
        if line.startswith((".pytest_cache/", "__pycache__/")):
            continue
        counts[line] = counts.get(line, 0) + 1
    modules = [m for m, _ in sorted(counts.items(), key=lambda x: -x[1])[:8]]

    snap = {
        "head": head,
        "branch": branch,
        "recent_commits": commits,
        "modified_modules": modules,
        "generated_at": int(time.time()),
    }

    out = Path(".git_snapshot")
    out.mkdir(exist_ok=True)
    target = out / "SUMMARY.json"
    target.write_text(json.dumps(snap, indent=2, ensure_ascii=False))

    print(f"snapshot → {target}")
    print(f"  head: {head or '(none)'}")
    print(f"  branch: {branch or '(none)'}")
    print(f"  commits: {len(commits)}")
    print(f"  modules: {len(modules)}")
    print(f"  size: {target.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
