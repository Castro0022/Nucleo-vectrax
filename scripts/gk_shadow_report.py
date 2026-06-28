#!/usr/bin/env python3
"""
Gravity Kernel — Reporte de la ventana shadow
===============================================
Lee los registros obs_type='shadow_eval' del observation_ledger y genera el
resumen de la ventana controlada:

  * coincidencias vs divergencias con la acción real
  * reasons más frecuentes
  * promedio de rhythm_score y cause_effect_score
  * casos rhythm_score < 0.3 donde el sistema respondió
  * casos cause_effect_score < 0.45 donde el sistema respondió

Uso:
  python3 scripts/gk_shadow_report.py [--limit 50] [--db <path>]

Solo lectura. No modifica el ledger ni el sistema.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import Counter

_DEFAULT_DB = os.path.join(
    os.environ.get("VECTRAX_VAULT_DIR", os.path.expanduser("~/Vectrax/vault")),
    "observation_ledger.db",
)
RHYTHM_FLOOR = 0.30
CAUSE_EFFECT_FLOOR = 0.45


def _load(db_path: str, limit: int) -> list:
    c = sqlite3.connect(db_path, timeout=5)
    c.row_factory = sqlite3.Row
    try:
        rows = c.execute(
            "SELECT * FROM autonomous_observations WHERE obs_type='shadow_eval' "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        c.close()
    out = []
    for r in rows:
        ev = r["evidence"]
        try:
            ev = json.loads(ev) if isinstance(ev, str) else ev
        except (json.JSONDecodeError, TypeError):
            ev = {}
        out.append(ev or {})
    return out


def report(db_path: str, limit: int) -> None:
    if not os.path.exists(db_path):
        print(f"[!] No existe el ledger: {db_path}")
        return
    records = _load(db_path, limit)
    n = len(records)
    print(f"=== Gravity Kernel — ventana shadow ===")
    print(f"ledger: {db_path}")
    print(f"registros shadow_eval analizados: {n}")
    if n == 0:
        print("\n(Sin registros todavía: el worker necesita procesar mensajes reales\n"
              " con VX_GRAVITY_KERNEL_SHADOW=1 para poblar la ventana.)")
        return

    agree = sum(1 for r in records if r.get("agreement") is True)
    diverge = n - agree

    by_source = Counter(r.get("source", "unknown") for r in records)
    reasons = Counter()
    rhythm_vals, ce_vals = [], []
    low_rhythm_responded = []
    low_ce_responded = []

    for r in records:
        eo = r.get("eval_object", {}) or {}
        reasons[eo.get("reason", "(sin reason)")] += 1
        rs = eo.get("rhythm_score")
        cs = eo.get("cause_effect_score")
        if isinstance(rs, (int, float)):
            rhythm_vals.append(rs)
        if isinstance(cs, (int, float)):
            ce_vals.append(cs)
        responded = r.get("actual_action") == "respond"
        if responded and isinstance(rs, (int, float)) and rs < RHYTHM_FLOOR:
            low_rhythm_responded.append(r)
        if responded and isinstance(cs, (int, float)) and cs < CAUSE_EFFECT_FLOOR:
            low_ce_responded.append(r)

    def _avg(xs):
        return round(sum(xs) / len(xs), 4) if xs else None

    print(f"\ncoincidencias (agreement=True): {agree}  ({agree*100//n}%)")
    print(f"divergencias  (agreement=False): {diverge}  ({diverge*100//n}%)")

    print(f"\npor source:")
    for src, cnt in by_source.most_common():
        print(f"  {cnt:>3}×  {src}")

    print(f"\nreasons más frecuentes:")
    for reason, cnt in reasons.most_common(8):
        print(f"  {cnt:>3}×  {reason}")

    print(f"\npromedio rhythm_score:       {_avg(rhythm_vals)}")
    print(f"promedio cause_effect_score: {_avg(ce_vals)}")

    print(f"\nrhythm_score < {RHYTHM_FLOOR} PERO el sistema respondió: {len(low_rhythm_responded)}")
    for r in low_rhythm_responded[:10]:
        eo = r.get("eval_object", {})
        sg = r.get("signals", {})
        print(f"  rhythm={eo.get('rhythm_score')} action={eo.get('rhythm_action')} "
              f"corr={sg.get('corrections_recent')} frust={sg.get('frustration')} "
              f"misread={sg.get('system_misread')}")

    print(f"\ncause_effect_score < {CAUSE_EFFECT_FLOOR} PERO el sistema respondió: {len(low_ce_responded)}")
    for r in low_ce_responded[:10]:
        eo = r.get("eval_object", {})
        sg = r.get("signals", {})
        print(f"  cause_effect={eo.get('cause_effect_score')} action={eo.get('cause_effect_action')} "
              f"action_type={sg.get('action_type')} pattern_stats={sg.get('pattern_stats_present')}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Reporte de la ventana shadow del Gravity Kernel")
    ap.add_argument("--limit", type=int, default=50, help="máximo de registros a analizar")
    ap.add_argument("--db", default=_DEFAULT_DB, help="ruta del observation_ledger.db")
    args = ap.parse_args()
    report(args.db, args.limit)


if __name__ == "__main__":
    main()
