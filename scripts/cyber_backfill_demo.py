#!/usr/bin/env python3
"""
scripts/cyber_backfill_demo.py — Backfill instrumentado (demostración técnica).

Reporta RENDIMIENTO por ventana (tiempo, CVE/s, CPU%, RAM RSS, disco libre,
tamaño de las BD del dominio, progreso %) y, al final, el RESULTADO COGNITIVO
(eventos observados, outcomes, estrellas cognitivas y el ratio CVE→estrella que
evidencia que 1 CVE ≠ 1 estrella).

El reporte final usa fuentes BARATAS (seen_ledger.counts() por SQL + conteo de
estrellas 'cybersecurity:' en gravity), sin cargar el ledger JSONL completo.

Modos:
  --since / --until YYYY-MM-DD   rango (default 2020-01-01 → hoy)
  --interrupt-after N            levanta una interrupción tras N ventanas (prueba de resume)
  --no-resume                    ignora el checkpoint (fuerza reproceso)

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))
except Exception:
    pass

from connectors.cybersecurity.backfill import run_backfill
from connectors.cybersecurity import seen_ledger

_HOME = os.path.expanduser("~")
_GRAV = os.path.join(_HOME, ".vectrax", "gravity_index.json")


class _Interrupt(Exception):
    """Interrupción deliberada para probar el checkpoint reanudable."""


def _rss_mb() -> float:
    try:
        out = subprocess.check_output(["ps", "-o", "rss=", "-p", str(os.getpid())])
        return round(int(out.strip()) / 1024, 1)
    except Exception:
        return -1.0


def _cyber_stars() -> int:
    try:
        d = json.load(open(_GRAV))
        return sum(1 for k in d if k.startswith("cybersecurity:"))
    except Exception:
        return -1


def _mb(path: str) -> float:
    try:
        return round(os.path.getsize(path) / 1e6, 2)
    except Exception:
        return 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--interrupt-after", type=int, default=0)
    ap.add_argument("--no-resume", action="store_true")
    a = ap.parse_args()

    since = datetime.strptime(a.since, "%Y-%m-%d").replace(tzinfo=timezone.utc) if a.since else None
    until = datetime.strptime(a.until, "%Y-%m-%d").replace(tzinfo=timezone.utc) if a.until else None

    st = {"pcpu": sum(os.times()[:2]), "pwall": time.time(), "peak_rss": 0.0, "n": 0}
    vledger_path = os.path.join(seen_ledger._vault_dir(), "domain_verification", "cybersecurity.jsonl")

    def cb(info):
        now = time.time()
        cpu = sum(os.times()[:2])
        dwall = max(now - st["pwall"], 1e-6)
        cpu_pct = round((cpu - st["pcpu"]) / dwall * 100, 1)
        st["pwall"], st["pcpu"] = now, cpu
        rss = _rss_mb()
        st["peak_rss"] = max(st["peak_rss"], rss)
        st["n"] += 1
        du = shutil.disk_usage("/")
        cve, el = info["cve_seen"], info["elapsed_s"]
        row = {
            "win": "%d/%d" % (info["window_index"], info["windows_total"]),
            "progress_pct": round(info["window_index"] / max(info["windows_total"], 1) * 100, 1),
            "cve_seen": cve,
            "cve_per_s": round(cve / max(el, 1e-6), 1),
            "elapsed_s": el,
            "cpu_pct": cpu_pct,
            "rss_mb": rss,
            "disk_free_gb": round(du.free / 1e9, 1),
            "grav_mb": _mb(_GRAV),
            "seen_db_mb": _mb(seen_ledger._db_path()),
            "vledger_mb": _mb(vledger_path),
            "cyber_stars": _cyber_stars(),
        }
        print("PROGRESS " + json.dumps(row), flush=True)
        if a.interrupt_after and info["window_index"] >= a.interrupt_after:
            raise _Interrupt()

    t0 = time.time()
    interrupted = False
    summary = None
    try:
        summary = run_backfill(since=since, until=until, resume=not a.no_resume, progress_cb=cb)
    except _Interrupt:
        interrupted = True

    total = round(time.time() - t0, 2)
    counts = seen_ledger.counts()
    obs = int(counts.get("total", 0))
    stars = _cyber_stars()
    report = {
        "interrupted": interrupted,
        "windows_processed": st["n"],
        "performance": {
            "total_s": total,
            "cve_observed": obs,
            "avg_cve_per_s": round(obs / max(total, 1e-6), 1),
            "peak_rss_mb": st["peak_rss"],
            "grav_mb": _mb(_GRAV),
            "seen_db_mb": _mb(seen_ledger._db_path()),
            "vledger_mb": _mb(vledger_path),
        },
        "cognitive": {
            "cve_observed": obs,
            "by_outcome": counts.get("by_outcome", {}),
            "in_kev_wins": int(counts.get("in_kev", 0)),
            "cognitive_stars": stars,
            "cve_per_star": round(obs / stars, 1) if stars and stars > 0 else None,
        },
        "backfill_summary": summary,
    }
    print("REPORT " + json.dumps(report, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
