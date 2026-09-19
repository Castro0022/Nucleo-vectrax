"""
core/argos_migration.py — Integración de memoria histórica con procedencia
=============================================================================
Importa la memoria histórica del núcleo original de Vectrax
(`~/Vectrax_Sistema_Núcleo/Transmedia/ARGOS/*.md` y
`~/Vectrax_Sistema_Núcleo/Sinapsis/memoria_dialogo.json`) hacia `vectrax.db`,
reutilizando la política de filtrado YA EXISTENTE en `core.argos_ingesta
.evaluar_ingesta()` — no se reimplementa ningún filtro nuevo.

Reglas duras (plan de reunificación, FASE 2.3):
  - Solo los veredictos ACEPTAR se insertan, vía `vectrax.engine.ingest()`
    con `channel="creator"`, `owner="mario"` — la memoria histórica del
    creador entra al mismo canal que su memoria moderna, con procedencia
    explícita (`source_origin`, `source_path`, `imported_at`), nunca mezclada
    sin marca.
  - CUARENTENA y DESCARTAR se registran en un reporte para revisión humana;
    NUNCA se insertan, y los archivos/notas originales NUNCA se modifican
    ni se borran (`dry_run=True` por defecto).
  - Deduplicación: se reutiliza el `near_dupe` por embeddings YA presente
    en `engine.ingest()` (umbral 0.95) — sin mecanismo nuevo.

Creado: 2026-09-19 — reunificación de Vectrax (rama nucleo/reunificacion).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List

from core.argos_ingesta import Intencion, Origen, Veredicto, evaluar_ingesta
from vectrax.identity import CHANNEL_CREATOR, CREATOR_OWNER

logger = logging.getLogger("vectrax.argos_migration")

ARGOS_DIR = Path.home() / "Vectrax_Sistema_Núcleo" / "Transmedia" / "ARGOS"
SINAPSIS_DIR = Path.home() / "Vectrax_Sistema_Núcleo" / "Sinapsis"
MEMORIA_DIALOGO_PATH = SINAPSIS_DIR / "memoria_dialogo.json"

_DB_PATH = Path.home() / ".vectrax" / "vectrax.db"


def _ensure_provenance_columns() -> None:
    """Migración aditiva: añade columnas de procedencia a `stars` si no
    existen. Nunca altera ni borra columnas existentes (mismo idioma que
    `vectrax.db._migrate_db()`)."""
    additions = [
        "source_origin TEXT NOT NULL DEFAULT ''",
        "source_path TEXT NOT NULL DEFAULT ''",
        "imported_at REAL NOT NULL DEFAULT 0",
    ]
    conn = sqlite3.connect(_DB_PATH)
    try:
        for col_def in additions:
            try:
                conn.execute(f"ALTER TABLE stars ADD COLUMN {col_def}")
            except Exception:
                pass  # ya existe — idempotente
        conn.commit()
    finally:
        conn.close()


def _tag_provenance(star_id: str, source_origin: str, source_path: str) -> None:
    conn = sqlite3.connect(_DB_PATH)
    try:
        conn.execute(
            "UPDATE stars SET source_origin=?, source_path=?, imported_at=? WHERE id=?",
            (source_origin, source_path, time.time(), star_id),
        )
        conn.commit()
    finally:
        conn.close()


def _load_argos_notes() -> List[Dict[str, str]]:
    notes = []
    if not ARGOS_DIR.exists():
        return notes
    for path in sorted(ARGOS_DIR.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            logger.warning("No pude leer %s: %s", path, exc)
            continue
        notes.append({"content": text, "path": str(path)})
    return notes


def _load_memoria_dialogo() -> List[Dict[str, str]]:
    entries = []
    if not MEMORIA_DIALOGO_PATH.exists():
        return entries
    try:
        data = json.loads(MEMORIA_DIALOGO_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("No pude leer %s: %s", MEMORIA_DIALOGO_PATH, exc)
        return entries
    if not isinstance(data, list):
        return entries
    for i, item in enumerate(data):
        pregunta = (item or {}).get("pregunta", "")
        respuesta = (item or {}).get("respuesta", "")
        content = f"P: {pregunta}\nR: {respuesta}".strip()
        entries.append({
            "content": content,
            "path": f"{MEMORIA_DIALOGO_PATH}#{i}",
        })
    return entries


def import_historical_memory(dry_run: bool = True) -> Dict[str, Any]:
    """Clasifica e importa la memoria histórica ARGOS + Sinapsis.

    Args:
        dry_run: si True (default), SOLO clasifica y reporta — no inserta
            nada en `vectrax.db`. Si False, inserta los veredictos ACEPTAR.

    Returns:
        Reporte con conteos por veredicto, y detalle de items aceptados/
        descartados/en cuarentena, para revisión humana. NUNCA modifica ni
        borra los archivos originales.
    """
    if not dry_run:
        _ensure_provenance_columns()

    sources: List[Dict[str, str]] = []
    for n in _load_argos_notes():
        n["source_kind"] = "argos_note"
        sources.append(n)
    for n in _load_memoria_dialogo():
        n["source_kind"] = "sinapsis_memoria_dialogo"
        sources.append(n)

    report: Dict[str, Any] = {
        "dry_run": dry_run,
        "total_evaluated": len(sources),
        "aceptar": 0,
        "cuarentena": 0,
        "descartar": 0,
        "inserted_star_ids": [],
        "quarantine_sample": [],
        "discard_sample": [],
        "errors": [],
    }

    for item in sources:
        content = item["content"]
        path = item["path"]
        try:
            result = evaluar_ingesta(content, origen=Origen.IMPORTACION, source_path=path)
        except Exception as exc:
            report["errors"].append({"path": path, "error": str(exc)[:200]})
            continue

        if result.veredicto == Veredicto.ACEPTAR:
            report["aceptar"] += 1
            if not dry_run:
                try:
                    from vectrax.engine import ingest
                    star = ingest(text=content, channel=CHANNEL_CREATOR, owner=CREATOR_OWNER)
                    _tag_provenance(star.id, item["source_kind"], path)
                    report["inserted_star_ids"].append(star.id)
                except Exception as exc:
                    report["errors"].append({"path": path, "error": f"ingest_failed: {exc}"[:200]})
        elif result.veredicto == Veredicto.CUARENTENA:
            report["cuarentena"] += 1
            if len(report["quarantine_sample"]) < 20:
                report["quarantine_sample"].append({"path": path, "razon": result.razon})
        else:  # DESCARTAR
            report["descartar"] += 1
            if len(report["discard_sample"]) < 20:
                report["discard_sample"].append({"path": path, "razon": result.razon})

    return report


def write_report(report: Dict[str, Any], out_path: Path) -> None:
    out_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8",
    )
