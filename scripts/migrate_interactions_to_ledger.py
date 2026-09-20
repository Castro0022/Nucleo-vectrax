#!/usr/bin/env python3
"""
PARTE 7 (2026-09-20) — Migración segura desde `user_memory.db::interactions`
al ledger conversacional canónico (`core.memory.conversation_ledger`).

Contexto: antes del ledger append-only (PARTE 1 de la reunificación de
memoria), cada turno usuario↔Vectrax se guardaba SOLO en la tabla
`interactions` de `vault/user_memory.db` (una fila = user_input + bot_output
combinados, sin message_id/correlation_id). Ese historial preexistente
(269 filas al momento de escribir este script) nunca llegó al ledger
canónico porque el ledger no existía todavía cuando se escribió.

Este script:
  1. NUNCA borra ni modifica `interactions` (ni ninguna otra tabla de
     `user_memory.db`) — solo LEE de ella.
  2. Por cada fila, crea hasta 2 eventos en `conversation_events`
     (role="user" desde `user_input`, role="assistant" desde `bot_output`),
     usando el MISMO mecanismo de idempotencia ya existente en el ledger
     (`ConversationEvent.idempotency_key` — fallback por hash de
     contenido+bucket de tiempo de 2s, ya que `interactions` no tiene
     message_id/correlation_id). Esto hace el script SEGURO de re-ejecutar:
     una segunda corrida no duplica nada, `append()` devuelve el evento ya
     existente.
  3. Deduplica SOLO por esa clave demostrable — nunca por heurísticas de
     similitud de texto ni por "parece la misma conversación".
  4. Resuelve identidades crudas (p.ej. "tg:2030762343", "owner") a su
     owner canónico reutilizando EXACTAMENTE los 2 mecanismos que el resto
     del sistema ya usa (`vectrax.identity_aliases.resolve_owner()` +
     detección de UID de Telegram del creador vía `VX_CREATOR_ID`, mismo
     patrón que `core.nucleus.nucleus_authority._decide()`), sin inventar
     ni hardcodear ningún nombre nuevo.
  5. Reporta conteos ANTES/DESPUÉS (interactions leídas, eventos ya
     presentes, eventos nuevos creados, total del ledger antes/después).

Dry-run por defecto (no escribe nada). Pasar --apply para escribir de verdad.

Uso:
    python3 scripts/migrate_interactions_to_ledger.py            # dry-run
    python3 scripts/migrate_interactions_to_ledger.py --apply    # escribe
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_USER_MEMORY_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "user_memory.db",
)

_MIGRATION_SOURCE = "migration:user_memory.interactions"


# ---------------------------------------------------------------------------
# Resolución de identidad canónica -- SIN hardcodear nombres nuevos
# ---------------------------------------------------------------------------

def _resolve_canonical_owner(raw_user_id: str) -> str:
    """Resuelve la identidad canónica para `raw_user_id`.

    Reutiliza EXACTAMENTE los 2 mecanismos ya existentes en el sistema (el
    mismo par de pasos que `core.nucleus.nucleus_authority.NucleusAuthority
    ._decide()` ya aplica en producción para tráfico en vivo), en vez de
    inventar una tercera fuente de verdad para identidad:

      1. `vectrax.identity_aliases.resolve_owner()` -- tabla aditiva
         `identity_aliases` (hoy: "owner" -> "mario"). Fail-safe: si no hay
         alias registrado, devuelve el mismo valor sin cambios.
      2. UID de Telegram del creador (`VX_CREATOR_ID`, mismo default
         "2030762343" que ya usa el resto del sistema) -- "tg:<id>" o
         "<id>" (sin prefijo) que coincidan se resuelven a
         `vectrax.identity.CREATOR_OWNER`.

    Nunca lanza; ante cualquier fallo de import devuelve `raw_user_id` sin
    cambios (mismo criterio fail-safe que `resolve_owner()`).
    """
    if not raw_user_id:
        return raw_user_id
    canonical = raw_user_id
    try:
        from vectrax.identity_aliases import resolve_owner
        canonical = resolve_owner(raw_user_id)
    except Exception:
        pass
    try:
        from vectrax.identity import CREATOR_OWNER
        creator_uid = os.environ.get("VX_CREATOR_ID", "2030762343")
        norm = (canonical or "").replace("tg:", "")
        if norm and norm == creator_uid:
            return CREATOR_OWNER
    except Exception:
        pass
    return canonical


# ---------------------------------------------------------------------------
# Lectura read-only de `interactions`
# ---------------------------------------------------------------------------

def _read_interactions(db_path: str) -> List[Dict[str, Any]]:
    """Lee TODAS las filas de `interactions`, ordenadas cronológicamente.
    Read-only -- nunca escribe ni borra en `user_memory.db`."""
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, user_id, user_input, bot_output, timestamp "
            "FROM interactions ORDER BY timestamp ASC, id ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _existing_idempotency_keys(db_path: str) -> set:
    """Snapshot read-only de las claves de idempotencia YA presentes en el
    ledger -- usado para reportar honestamente cuántos eventos son NUEVOS
    vs ya migrados, incluso en modo dry-run (donde nunca se llama a
    `append()`)."""
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        rows = conn.execute(
            "SELECT idempotency_key FROM conversation_events"
        ).fetchall()
        return {r[0] for r in rows}
    except sqlite3.OperationalError:
        # Tabla `conversation_events` todavía no existe (ledger nunca se
        # inicializó en este archivo) -- equivalente a "ningún evento aún".
        return set()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Construcción de eventos (sin escribir todavía)
# ---------------------------------------------------------------------------

def _build_events_for_row(row: Dict[str, Any]):
    """Construye hasta 2 `ConversationEvent` (user/assistant) a partir de una
    fila de `interactions`. Devuelve (user_event|None, assistant_event|None,
    canonical_owner). No escribe nada -- solo construye objetos en memoria."""
    from core.memory.conversation_ledger import ConversationEvent

    raw_owner = row.get("user_id") or ""
    canonical_owner = _resolve_canonical_owner(raw_owner)
    ts = float(row.get("timestamp") or 0.0)

    user_text = (row.get("user_input") or "").strip()
    bot_text = (row.get("bot_output") or "").strip()

    user_event = None
    if user_text:
        user_event = ConversationEvent(
            user_id=canonical_owner, role="user", content=user_text,
            timestamp=ts, status="received", source=_MIGRATION_SOURCE,
        )

    assistant_event = None
    if bot_text:
        assistant_event = ConversationEvent(
            # +0.0001s: mismo timestamp lógico del turno, pero garantiza
            # orden determinista user->assistant ante empates exactos al
            # ordenar por timestamp (nunca se reescribe el dato original).
            user_id=canonical_owner, role="assistant", content=bot_text,
            timestamp=ts + 0.0001, status="delivered", source=_MIGRATION_SOURCE,
        )

    return user_event, assistant_event, raw_owner, canonical_owner


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------

@dataclass
class MigrationReport:
    interactions_total: int = 0
    interactions_skipped_empty_owner: int = 0
    candidate_user_events: int = 0
    candidate_assistant_events: int = 0
    already_present_user_events: int = 0
    already_present_assistant_events: int = 0
    new_user_events: int = 0
    new_assistant_events: int = 0
    distinct_raw_owners: int = 0
    distinct_canonical_owners: int = 0
    owner_map: Dict[str, str] = field(default_factory=dict)
    ledger_count_before: int = 0
    ledger_count_after: int = 0

    @property
    def new_events_total(self) -> int:
        return self.new_user_events + self.new_assistant_events

    @property
    def already_present_total(self) -> int:
        return self.already_present_user_events + self.already_present_assistant_events


# ---------------------------------------------------------------------------
# Migración
# ---------------------------------------------------------------------------

def migrate(
    db_path: str = _USER_MEMORY_DB,
    apply: bool = False,
    ledger: Optional[Any] = None,
) -> MigrationReport:
    """Ejecuta la migración (o su simulación en dry-run).

    `ledger`: repositorio del ledger a usar -- por defecto el singleton real
    (`get_conversation_ledger()`, misma `vault/user_memory.db`). Los tests
    inyectan una instancia aislada apuntando a un archivo temporal.

    Garantías:
      - `interactions` NUNCA se modifica ni se borra (solo se lee).
      - Deduplicación SOLO por `idempotency_key` (clave demostrable, misma
        que ya usa el ledger para tráfico en vivo) -- nunca heurística.
      - Re-ejecutar este script (con o sin --apply) es seguro: no duplica
        eventos ya migrados.
    """
    from core.memory.conversation_ledger import get_conversation_ledger

    if ledger is None:
        ledger = get_conversation_ledger()

    report = MigrationReport()
    rows = _read_interactions(db_path)
    report.interactions_total = len(rows)
    report.ledger_count_before = ledger.count_total()

    existing_keys = _existing_idempotency_keys(db_path)
    raw_owners: set = set()
    owner_map: Dict[str, str] = {}

    for row in rows:
        user_event, assistant_event, raw_owner, canonical_owner = _build_events_for_row(row)

        if not raw_owner:
            report.interactions_skipped_empty_owner += 1
            continue

        raw_owners.add(raw_owner)
        owner_map[raw_owner] = canonical_owner

        real_user_event_id = ""

        if user_event is not None:
            is_dup = user_event.idempotency_key in existing_keys
            report.candidate_user_events += 1
            if is_dup:
                report.already_present_user_events += 1
            else:
                report.new_user_events += 1
            # Se actualiza `existing_keys` SIEMPRE (incluso en dry-run) --
            # no solo cuando `apply=True`. Dos filas DISTINTAS de
            # `interactions` pueden colisionar entre SÍ (mismo usuario,
            # mismo contenido, dentro de la misma ventana de 2s del
            # fallback de idempotencia) sin colisionar con nada YA
            # presente en el ledger -- sin este tracking progresivo, el
            # dry-run subestima cuántos eventos son realmente nuevos y
            # diverge del resultado real de --apply.
            existing_keys.add(user_event.idempotency_key)
            if apply:
                real_user_event_id = ledger.append(user_event)

        if assistant_event is not None:
            is_dup = assistant_event.idempotency_key in existing_keys
            report.candidate_assistant_events += 1
            if is_dup:
                report.already_present_assistant_events += 1
            else:
                report.new_assistant_events += 1
            existing_keys.add(assistant_event.idempotency_key)
            if apply:
                if real_user_event_id:
                    assistant_event.reply_to_event_id = real_user_event_id
                ledger.append(assistant_event)

    report.distinct_raw_owners = len(raw_owners)
    report.distinct_canonical_owners = len(set(owner_map.values()))
    report.owner_map = owner_map
    report.ledger_count_after = ledger.count_total() if apply else report.ledger_count_before

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_report(report: MigrationReport, applied: bool) -> None:
    print("=" * 70)
    print("MIGRACIÓN interactions -> conversation_ledger (PARTE 7)")
    print("=" * 70)
    print(f"Modo:                                          {'APLICADO' if applied else 'DRY-RUN (sin escritura)'}")
    print(f"Filas leídas en interactions (sin modificar):  {report.interactions_total}")
    print(f"  omitidas (user_id vacío):                    {report.interactions_skipped_empty_owner}")
    print()
    print(f"Owners crudos distintos encontrados:           {report.distinct_raw_owners}")
    print(f"Owners canónicos resultantes:                  {report.distinct_canonical_owners}")
    remapped = {k: v for k, v in report.owner_map.items() if k != v}
    if remapped:
        print("  Remapeos aplicados (raw -> canónico):")
        for raw, canon in sorted(remapped.items()):
            print(f"    {raw!r} -> {canon!r}")
    print()
    print(f"Eventos 'user' candidatos:                     {report.candidate_user_events}")
    print(f"  ya presentes (idempotency_key existente):    {report.already_present_user_events}")
    print(f"  nuevos:                                      {report.new_user_events}")
    print(f"Eventos 'assistant' candidatos:                {report.candidate_assistant_events}")
    print(f"  ya presentes (idempotency_key existente):    {report.already_present_assistant_events}")
    print(f"  nuevos:                                      {report.new_assistant_events}")
    print()
    print(f"conversation_events ANTES:                     {report.ledger_count_before}")
    print(f"conversation_events DESPUÉS:                   {report.ledger_count_after}"
          + ("" if applied else "  (=ANTES -- dry-run, sin cambios)"))
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Escribir en el ledger (default: dry-run)")
    parser.add_argument("--db-path", default=_USER_MEMORY_DB, help="Ruta a user_memory.db")
    args = parser.parse_args()

    # Si se pasa --db-path distinto del default, el ledger también debe
    # apuntar a ese MISMO archivo (misma DB física -- `conversation_events`
    # vive junto a `interactions` en `user_memory.db`), nunca al singleton
    # de producción por defecto.
    ledger = None
    if args.db_path != _USER_MEMORY_DB:
        from core.memory.conversation_ledger import SQLiteConversationLedger
        ledger = SQLiteConversationLedger(db_path=args.db_path)

    report = migrate(db_path=args.db_path, apply=args.apply, ledger=ledger)
    print_report(report, applied=args.apply)

    if not args.apply:
        print("\nDry-run únicamente -- no se escribió nada. Re-ejecutar con --apply para migrar.")
    else:
        print("\nMigración aplicada. `interactions` no fue modificada ni borrada.")


if __name__ == "__main__":
    main()
