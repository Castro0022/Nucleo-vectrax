"""
connectors/cybersecurity/backfill.py — Orquestador de backfill NVD + CISA KEV.

Flujo: KEV (snapshot + mapa) → NVD por ventanas de fecha (≥2020-01-01, ≤120 días,
paginadas) → normalización → verificación (outcome por nivel de subject) + masa
de estrellas (bulk, una escritura del índice por ventana). Reanudable por
checkpoint (índice de ventana) e idempotente por seen-ledger (`cve_id`): re-correr
añade 0 líneas y 0 masa.

`--dry-run` NO escribe (ni gravity, ni verification_ledger, ni seen-ledger): solo
mide cobertura de dimensiones y distribución de outcomes y las reporta.

Uso:
    python -m connectors.cybersecurity.backfill --dry-run --since 2021-01-01 --until 2021-05-01
    python -m connectors.cybersecurity.backfill                      # backfill real 2020→hoy

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from connectors.cybersecurity import kev_client, seen_ledger
from connectors.cybersecurity import dimensions as dim
from connectors.cybersecurity import verification_cycle as vc
from connectors.cybersecurity.nvd_provider import (
    MIN_PUB, NvdClient, date_windows, normalize_cve,
)

logger = logging.getLogger("vectrax.cybersecurity.backfill")

_CKPT_WINDOW = "backfill_window_idx"


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _resolve_kev_map(kev_map: Optional[Dict[str, Any]], fetch: bool) -> Dict[str, Any]:
    if kev_map is not None:
        return kev_map
    kev_json: Dict[str, Any] = {}
    if fetch:
        try:
            kev_json = kev_client.fetch_kev()
        except Exception as exc:
            logger.warning("fetch_kev falló (%s); intento snapshot local", exc)
            kev_json = kev_client.load_kev()
    else:
        kev_json = kev_client.load_kev()
    return kev_client.build_kev_map(kev_json)


def run_backfill(
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    dry_run: bool = False,
    now: Optional[datetime] = None,
    client: Any = None,
    kev_map: Optional[Dict[str, Any]] = None,
    gravity_index: Any = None,
    fetch_kev_if_missing: bool = True,
    max_windows: Optional[int] = None,
    resume: bool = True,
    progress_cb: Any = None,
) -> Dict[str, Any]:
    """Ejecuta el backfill (o el dry-run). Devuelve un summary con cifras."""
    t0 = time.time()
    now_dt = now or datetime.now(timezone.utc)
    since_dt = since or MIN_PUB
    until_dt = until or now_dt
    # Checkpoint POR RANGO: el índice de ventana solo es válido para el mismo
    # (since, until). Así un backfill de otro rango no corrompe la reanudación.
    ckpt_key = f"{_CKPT_WINDOW}:{since_dt.date()}:{until_dt.date()}"

    kev_map = _resolve_kev_map(kev_map, fetch_kev_if_missing)
    client = client or NvdClient()
    windows = date_windows(since_dt, until_dt)
    if max_windows:
        windows = windows[:max_windows]

    # Reanudación (solo en modo real).
    resume_idx = 0
    if resume and not dry_run:
        try:
            resume_idx = int(seen_ledger.get_checkpoint(ckpt_key, "0") or 0)
        except ValueError:
            resume_idx = 0

    gi = None
    records: Optional[Dict[str, Any]] = None
    if not dry_run:
        if gravity_index is None:
            from core.learn.gravity_engine import get_gravity_index
            gravity_index = get_gravity_index()
        gi = gravity_index
        records = gi.load_raw()

    n_cve = 0
    dims_rows: List[Dict[str, Any]] = []
    outcome_counts: Counter = Counter()

    for wi, (ws, we) in enumerate(windows):
        if wi < resume_idx:
            continue
        batch = []
        for item in client.iter_window(ws, we):
            ev = normalize_cve(item, kev_map)
            n_cve += 1
            if dry_run:
                r = vc.resolve_cve(ev, now_dt)
                if r["excluded"] or not r["cve_id"]:
                    continue
                dims_rows.append(r["dims"])
                outcome_counts[r["outcome"].status.value] += 1
            else:
                batch.append(ev)
        if not dry_run:
            vc.verify_events(batch, record=True, now=now_dt, gravity_records=records)
            gi.update_records(records)
            seen_ledger.set_checkpoint(ckpt_key, str(wi + 1))
        if progress_cb is not None:
            progress_cb({
                "window_index": wi + 1,
                "windows_total": len(windows),
                "cve_seen": n_cve,
                "elapsed_s": round(time.time() - t0, 2),
                "dry_run": dry_run,
            })

    elapsed = round(time.time() - t0, 2)
    if dry_run:
        return {
            "dry_run": True,
            "windows": len(windows),
            "cve_seen": n_cve,
            "coverage": dim.coverage(dims_rows),
            "outcomes": dict(outcome_counts),
            "elapsed_s": elapsed,
        }

    star_fps = [fp for fp in records.keys() if fp.startswith("cybersecurity:")]
    summary = {
        "dry_run": False,
        "windows": len(windows),
        "cve_seen": n_cve,
        "seen_ledger": seen_ledger.counts(),
        "cyber_stars": len(star_fps),
        "verified": vc.verified_score().to_dict(),
        "elapsed_s": elapsed,
    }
    logger.info("cyber.backfill | %s", summary)
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    p = argparse.ArgumentParser(description="Backfill NVD + CISA KEV para el dominio cybersecurity.")
    p.add_argument("--dry-run", action="store_true", help="No escribe; solo reporta cifras.")
    p.add_argument("--since", help="Fecha inicio YYYY-MM-DD (default 2020-01-01).")
    p.add_argument("--until", help="Fecha fin YYYY-MM-DD (default hoy).")
    p.add_argument("--vault", help="Aísla ledgers/seen/KEV en este directorio (VECTRAX_VAULT_DIR).")
    p.add_argument("--no-kev-fetch", action="store_true", help="Usa snapshot KEV local (no descarga).")
    p.add_argument("--max-windows", type=int, help="Límite de ventanas (para pruebas).")
    p.add_argument("--no-resume", action="store_true", help="Ignora el checkpoint de reanudación.")
    args = p.parse_args(argv)

    if args.vault:
        os.environ["VECTRAX_VAULT_DIR"] = args.vault
        os.environ.setdefault("CYBER_SEEN_DB", os.path.join(args.vault, "cyber_seen.db"))

    summary = run_backfill(
        since=_parse_date(args.since) if args.since else None,
        until=_parse_date(args.until) if args.until else None,
        dry_run=args.dry_run,
        fetch_kev_if_missing=not args.no_kev_fetch,
        max_windows=args.max_windows,
        resume=not args.no_resume,
    )
    print(json.dumps(summary, indent=2, default=str, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
