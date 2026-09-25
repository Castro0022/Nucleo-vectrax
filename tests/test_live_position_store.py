"""
tests/test_live_position_store.py — cobertura de
connectors/etoro/live_position_store.py: persistencia (roundtrip,
sobrevive a una relectura en frío) y que solo lo no-CLOSED cuenta como
abierto.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from connectors.etoro import live_position_store
from core.trading.contracts import (
    EntryThesis,
    OrderIntent,
    OrderAction,
    PositionRecord,
    PositionStatus,
)

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def _thesis(position_id="pos-1") -> EntryThesis:
    return EntryThesis(
        position_id=position_id, symbol="BTCUSD", side="buy", created_at=T0,
        thesis="t", invalidation_conditions=("c1",), confidence_at_entry=0.5,
        evidence_snapshot={}, evidence_snapshot_id="snap-1",
    )


def _record(position_id="pos-1", status=PositionStatus.HOLDING) -> PositionRecord:
    return PositionRecord(
        position_id=position_id, entry_thesis=_thesis(position_id), status=status,
        remaining_quantity=100.0, current_intent=None, last_execution=None, updated_at=T0,
    )


def _entry(position_id="pos-1", status=PositionStatus.HOLDING) -> live_position_store.LivePositionEntry:
    return live_position_store.LivePositionEntry(
        record=_record(position_id, status), broker_position_id="broker-pos-1",
        instrument_id=42, entry_price=50_000.0, hard_stop_price=49_000.0,
    )


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(live_position_store, "_LIVE_POSITIONS_FILE", str(tmp_path / "live.jsonl"))


class TestRoundtrip:
    def test_save_and_get_roundtrip(self):
        entry = _entry()
        live_position_store.save(entry)
        got = live_position_store.get("pos-1")
        assert got == entry

    def test_missing_position_returns_none(self):
        assert live_position_store.get("does-not-exist") is None

    def test_latest_write_wins(self):
        live_position_store.save(_entry(status=PositionStatus.HOLDING))
        live_position_store.save(_entry(status=PositionStatus.CLOSING))
        got = live_position_store.get("pos-1")
        assert got.record.status == PositionStatus.CLOSING

    def test_survives_a_cold_reread(self):
        """Simula un reinicio: sin nada en RAM, releer desde disco."""
        live_position_store.save(_entry())
        got = live_position_store.get("pos-1")  # cada get() relee el archivo entero
        assert got is not None
        assert got.broker_position_id == "broker-pos-1"


class TestOpenPositionsFiltersClosed:
    def test_closed_position_excluded(self):
        live_position_store.save(_entry("pos-1", PositionStatus.HOLDING))
        live_position_store.save(_entry("pos-2", PositionStatus.CLOSED))
        open_ids = {e.record.position_id for e in live_position_store.open_positions()}
        assert open_ids == {"pos-1"}

    def test_reconciling_still_counts_as_open(self):
        live_position_store.save(_entry("pos-1", PositionStatus.RECONCILING))
        open_ids = {e.record.position_id for e in live_position_store.open_positions()}
        assert open_ids == {"pos-1"}

    def test_empty_store_returns_empty_list(self):
        assert live_position_store.open_positions() == []
