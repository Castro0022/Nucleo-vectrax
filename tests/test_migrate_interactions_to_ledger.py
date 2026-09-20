"""
tests/test_migrate_interactions_to_ledger.py — PARTE 7 (2026-09-20)
==============================================================================
Migración segura desde `user_memory.db::interactions` al ledger
conversacional canónico. Verifica:

  1. `interactions` NUNCA se modifica ni se borra (antes == después).
  2. Dry-run no escribe nada en el ledger.
  3. --apply escribe eventos "user"/"assistant" nuevos y es IDEMPOTENTE:
     re-ejecutar no duplica nada (dedup por `idempotency_key`, clave
     demostrable, nunca heurística de similitud).
  4. Identidades crudas ("tg:<creator_uid>", alias registrado en
     `identity_aliases`) se resuelven al owner canónico reutilizando los
     mecanismos YA existentes -- sin nombres nuevos hardcodeados aquí.
  5. Conteos antes/después son correctos y honestos.

Todo se ejercita contra archivos SQLite temporales -- nunca contra
`vault/user_memory.db` real ni `~/.vectrax/vectrax.db` real.
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

migrate_mod = importlib.import_module("scripts.migrate_interactions_to_ledger")


# ---------------------------------------------------------------------------
# Fixtures: user_memory.db temporal con `interactions` poblada + ledger
# temporal aislado (mismo archivo, como en producción)
# ---------------------------------------------------------------------------

def _make_interactions_db(tmp_path, rows):
    """Crea un `user_memory.db` temporal con una tabla `interactions`
    poblada con `rows` (lista de tuplas user_id, user_input, bot_output,
    timestamp)."""
    db_path = str(tmp_path / "user_memory.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE interactions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT NOT NULL,
            user_input  TEXT NOT NULL DEFAULT '',
            bot_output  TEXT NOT NULL DEFAULT '',
            timestamp   REAL NOT NULL
        );
        """
    )
    conn.executemany(
        "INSERT INTO interactions (user_id, user_input, bot_output, timestamp) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db_path


def _make_ledger(db_path):
    from core.memory.conversation_ledger import SQLiteConversationLedger
    return SQLiteConversationLedger(db_path=db_path)


def _count_interactions(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
    finally:
        conn.close()


def _interactions_snapshot(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(r) for r in conn.execute(
                "SELECT id, user_id, user_input, bot_output, timestamp "
                "FROM interactions ORDER BY id"
            ).fetchall()
        ]
    finally:
        conn.close()


BASE_TS = time.time() - 86400 * 30  # hace 30 días, evita colisiones de bucket


@pytest.fixture(autouse=True)
def _isolated_identity_aliases_db(tmp_path, monkeypatch):
    """Aisla `vectrax.identity_aliases.DB_PATH` (normalmente
    `~/.vectrax/vectrax.db`) a un archivo temporal por test -- `migrate()`
    llama a `resolve_owner()` para CADA fila, y sin esto cada test tocaría
    (aunque sea de forma inocua, solo lectura + CREATE TABLE IF NOT EXISTS)
    la base de datos real de producción."""
    alias_db = tmp_path / "identity_aliases_isolated.db"
    monkeypatch.setattr(
        "vectrax.identity_aliases.DB_PATH", alias_db, raising=True,
    )
    yield alias_db


# ---------------------------------------------------------------------------
# 1. `interactions` nunca se modifica ni se borra
# ---------------------------------------------------------------------------

class TestNeverTouchesInteractions:
    def test_dry_run_leaves_interactions_untouched(self, tmp_path):
        rows = [
            ("u1", "hola", "hola, ¿en qué ayudo?", BASE_TS),
            ("u1", "¿qué hora es?", "no tengo esa info", BASE_TS + 10),
        ]
        db_path = _make_interactions_db(tmp_path, rows)
        before = _interactions_snapshot(db_path)

        ledger = _make_ledger(db_path)
        migrate_mod.migrate(db_path=db_path, apply=False, ledger=ledger)

        after = _interactions_snapshot(db_path)
        assert before == after
        assert _count_interactions(db_path) == 2

    def test_apply_leaves_interactions_untouched(self, tmp_path):
        rows = [("u1", "hola", "hola!", BASE_TS)]
        db_path = _make_interactions_db(tmp_path, rows)
        before = _interactions_snapshot(db_path)

        ledger = _make_ledger(db_path)
        migrate_mod.migrate(db_path=db_path, apply=True, ledger=ledger)

        after = _interactions_snapshot(db_path)
        assert before == after
        assert _count_interactions(db_path) == 1


# ---------------------------------------------------------------------------
# 2. Dry-run no escribe nada
# ---------------------------------------------------------------------------

class TestDryRunNoWrites:
    def test_dry_run_ledger_stays_empty(self, tmp_path):
        rows = [
            ("u1", "hola", "hola!", BASE_TS),
            ("u2", "¿qué sabes de mí?", "aún no tengo info tuya", BASE_TS + 5),
        ]
        db_path = _make_interactions_db(tmp_path, rows)
        ledger = _make_ledger(db_path)

        report = migrate_mod.migrate(db_path=db_path, apply=False, ledger=ledger)

        assert ledger.count_total() == 0
        assert report.ledger_count_before == 0
        assert report.ledger_count_after == 0
        # Pero el reporte SÍ debe anticipar cuántos eventos se crearían.
        assert report.new_user_events == 2
        assert report.new_assistant_events == 2


# ---------------------------------------------------------------------------
# 3. --apply crea eventos y es idempotente
# ---------------------------------------------------------------------------

class TestApplyAndIdempotency:
    def test_apply_creates_user_and_assistant_events(self, tmp_path):
        rows = [("u1", "hola vectrax", "hola, ¿en qué trabajamos?", BASE_TS)]
        db_path = _make_interactions_db(tmp_path, rows)
        ledger = _make_ledger(db_path)

        report = migrate_mod.migrate(db_path=db_path, apply=True, ledger=ledger)

        assert report.new_user_events == 1
        assert report.new_assistant_events == 1
        assert report.ledger_count_after == 2

        recent = ledger.get_recent(tenant_id="default", user_id="u1", limit=10)
        assert len(recent) == 2
        assert recent[0]["role"] == "user"
        assert recent[0]["content"] == "hola vectrax"
        assert recent[1]["role"] == "assistant"
        assert recent[1]["content"] == "hola, ¿en qué trabajamos?"
        assert recent[1]["reply_to_event_id"] == recent[0]["event_id"]
        assert recent[0]["source"] == "migration:user_memory.interactions"

    def test_rerun_is_idempotent_no_duplicates(self, tmp_path):
        rows = [
            ("u1", "hola", "hola!", BASE_TS),
            ("u1", "¿qué hablamos ayer?", "no tengo evidencia de eso", BASE_TS + 20),
            ("u2", "gracias", "de nada", BASE_TS + 40),
        ]
        db_path = _make_interactions_db(tmp_path, rows)
        ledger = _make_ledger(db_path)

        report1 = migrate_mod.migrate(db_path=db_path, apply=True, ledger=ledger)
        total_after_first = ledger.count_total()
        assert total_after_first == report1.new_events_total
        assert total_after_first > 0

        # Segunda corrida -- misma DB, mismo ledger, sin cambios de por medio.
        report2 = migrate_mod.migrate(db_path=db_path, apply=True, ledger=ledger)

        assert report2.new_user_events == 0
        assert report2.new_assistant_events == 0
        assert report2.already_present_user_events == report1.new_user_events
        assert report2.already_present_assistant_events == report1.new_assistant_events
        assert ledger.count_total() == total_after_first, (
            "Re-ejecutar la migración no debe duplicar eventos"
        )

    def test_apply_skips_rows_with_empty_user_id(self, tmp_path):
        rows = [
            ("", "mensaje huérfano", "respuesta huérfana", BASE_TS),
            ("u1", "hola", "hola!", BASE_TS + 5),
        ]
        db_path = _make_interactions_db(tmp_path, rows)
        ledger = _make_ledger(db_path)

        report = migrate_mod.migrate(db_path=db_path, apply=True, ledger=ledger)

        assert report.interactions_skipped_empty_owner == 1
        assert report.new_user_events == 1
        assert report.new_assistant_events == 1

    def test_empty_user_input_or_bot_output_does_not_create_empty_event(self, tmp_path):
        rows = [
            ("u1", "", "solo hubo respuesta", BASE_TS),
            ("u1", "solo hubo pregunta", "", BASE_TS + 5),
        ]
        db_path = _make_interactions_db(tmp_path, rows)
        ledger = _make_ledger(db_path)

        report = migrate_mod.migrate(db_path=db_path, apply=True, ledger=ledger)

        # 1 assistant-only (primera fila) + 1 user-only (segunda fila).
        assert report.new_user_events == 1
        assert report.new_assistant_events == 1
        assert ledger.count_total() == 2


# ---------------------------------------------------------------------------
# 4. Resolución de identidad canónica -- sin hardcodear nombres nuevos
# ---------------------------------------------------------------------------

class TestIdentityResolution:
    def test_alias_table_resolves_raw_owner(self, tmp_path):
        """Reutiliza identity_aliases.resolve_owner() -- se registra un alias
        de prueba GENÉRICO (no el nombre real de producción) para probar el
        mecanismo, no un caso hardcodeado del dominio real. La DB de alias ya
        está aislada por el fixture autouse `_isolated_identity_aliases_db`."""
        from vectrax.identity_aliases import add_alias
        add_alias("raw_test_alias", "canonical_test_user", verified_by="test")

        rows = [("raw_test_alias", "hola", "hola!", BASE_TS)]
        db_path = _make_interactions_db(tmp_path, rows)
        ledger = _make_ledger(db_path)

        report = migrate_mod.migrate(db_path=db_path, apply=True, ledger=ledger)

        assert report.owner_map["raw_test_alias"] == "canonical_test_user"
        recent = ledger.get_recent(
            tenant_id="default", user_id="canonical_test_user", limit=10,
        )
        assert len(recent) == 2

    def test_creator_telegram_uid_resolves_to_creator_owner(self, tmp_path, monkeypatch):
        """'tg:<VX_CREATOR_ID>' debe resolver a CREATOR_OWNER -- mismo
        mecanismo que ya usa NucleusAuthority en producción, no una regla
        nueva inventada aquí."""
        monkeypatch.setenv("VX_CREATOR_ID", "999999")
        from vectrax.identity import CREATOR_OWNER

        rows = [("tg:999999", "hola", "hola creador", BASE_TS)]
        db_path = _make_interactions_db(tmp_path, rows)
        ledger = _make_ledger(db_path)

        report = migrate_mod.migrate(db_path=db_path, apply=True, ledger=ledger)

        assert report.owner_map["tg:999999"] == CREATOR_OWNER
        recent = ledger.get_recent(
            tenant_id="default", user_id=CREATOR_OWNER, limit=10,
        )
        assert len(recent) == 2

    def test_non_creator_non_aliased_telegram_id_kept_as_is(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VX_CREATOR_ID", "999999")
        rows = [("tg:123456", "hola", "hola!", BASE_TS)]
        db_path = _make_interactions_db(tmp_path, rows)
        ledger = _make_ledger(db_path)

        report = migrate_mod.migrate(db_path=db_path, apply=True, ledger=ledger)

        assert report.owner_map["tg:123456"] == "tg:123456"


# ---------------------------------------------------------------------------
# 5. Conteos antes/después
# ---------------------------------------------------------------------------

class TestBeforeAfterCounts:
    def test_counts_are_accurate(self, tmp_path):
        rows = [
            ("u1", "a", "b", BASE_TS),
            ("u1", "c", "d", BASE_TS + 10),
            ("u2", "e", "f", BASE_TS + 20),
        ]
        db_path = _make_interactions_db(tmp_path, rows)
        ledger = _make_ledger(db_path)

        report = migrate_mod.migrate(db_path=db_path, apply=True, ledger=ledger)

        assert report.interactions_total == 3
        assert report.ledger_count_before == 0
        assert report.ledger_count_after == 6  # 3 filas x 2 eventos
        assert report.distinct_raw_owners == 2
        assert report.distinct_canonical_owners == 2
