"""
Tests for core/self_observation/observation_ledger.py's optional
``timestamp`` parameter on ``record()``.

Historical replay (domain_ingester.ingest_event(..., event_timestamp=...))
must reach the Observation Ledger too, not just Gravity — otherwise replayed
evidence would be dated with the ingestion time (e.g. 2026) while Gravity
reflects the real historical event time. DB isolation is handled by the
repo's autouse ``_hermetic_base`` fixture (tests/conftest.py), which
redirects ``observation_ledger._DB_PATH`` to a per-test temp vault.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from core.self_observation import observation_ledger as ol


@pytest.fixture(autouse=True)
def _init_ledger():
    """Each test gets a fresh temp DB (via conftest's hermetic fixture);
    the table must be created before record()/get_recent() can be used."""
    ol.init_ledger()


class TestRecordTimestamp:
    def test_omitted_timestamp_uses_now(self):
        ol.record(domain="gravity", obs_type="test_event", summary="s")
        row = ol.get_recent(1)[0]
        ts = datetime.fromisoformat(row["timestamp"])
        assert (datetime.now(timezone.utc) - ts).total_seconds() < 5

    def test_explicit_timestamp_is_used_verbatim_when_aware(self):
        ol.record(
            domain="gravity", obs_type="test_event", summary="s",
            timestamp="2019-03-15T00:00:00+00:00",
        )
        row = ol.get_recent(1)[0]
        assert row["timestamp"] == "2019-03-15T00:00:00+00:00"

    def test_naive_timestamp_normalized_to_utc_aware(self):
        """No UTC offset supplied (e.g. a historical dataset export) must
        still be stored as an unambiguous, parseable, UTC-aware value."""
        ol.record(
            domain="gravity", obs_type="test_event", summary="s",
            timestamp="2019-03-15T10:00:00",
        )
        row = ol.get_recent(1)[0]
        dt = datetime.fromisoformat(row["timestamp"])
        assert dt.tzinfo is not None
        assert dt.utcoffset() == timedelta(0)
        assert dt.hour == 10

    def test_aware_non_utc_timestamp_converted_to_utc(self):
        ol.record(
            domain="gravity", obs_type="test_event", summary="s",
            timestamp="2019-03-15T10:00:00+05:00",
        )
        row = ol.get_recent(1)[0]
        dt = datetime.fromisoformat(row["timestamp"])
        assert dt.utcoffset() == timedelta(0)
        assert dt.hour == 5

    def test_invalid_timestamp_falls_back_to_now(self):
        ol.record(
            domain="gravity", obs_type="test_event", summary="s",
            timestamp="not-a-date",
        )
        row = ol.get_recent(1)[0]
        ts = datetime.fromisoformat(row["timestamp"])
        assert (datetime.now(timezone.utc) - ts).total_seconds() < 5


class TestVictoriaCEvidence:
    """record_evidence()/get_evidence() — Victoria C cable de retorno.

    "BUSQUÉ → RECIBÍ EVIDENCIA → AHORA PUEDO APRENDERLA". Reutiliza
    autonomous_observations (RULE 2), keyed por fingerprint (RULE 3).
    """

    def test_record_evidence_writes_with_correct_domain_and_star_id(self):
        row_id = ol.record_evidence(
            "abc123fingerprint", "online", "La capital de Francia es París.",
            correlation_id="corr-1", source="duckduckgo",
            source_reference="https://example.com/paris",
            query="capital de Francia",
        )
        assert row_id != -1
        rows = ol.get_by_domain(ol.EVIDENCE_DOMAIN, limit=10)
        assert len(rows) == 1
        assert rows[0]["star_id"] == "abc123fingerprint"
        assert rows[0]["obs_type"] == "online"
        assert rows[0]["evidence"]["content"] == "La capital de Francia es París."
        assert rows[0]["evidence"]["correlation_id"] == "corr-1"
        assert rows[0]["evidence"]["source"] == "duckduckgo"
        assert rows[0]["evidence"]["scope"] == "GLOBAL"

    def test_get_evidence_retrieves_by_exact_fingerprint(self):
        ol.record_evidence("fp-A", "places", "Farmacia Cruz Verde a 200m.")
        ol.record_evidence("fp-B", "market", "BTC: $65,000.")
        result_a = ol.get_evidence("fp-A")
        result_b = ol.get_evidence("fp-B")
        assert len(result_a) == 1
        assert result_a[0]["evidence"]["content"] == "Farmacia Cruz Verde a 200m."
        assert len(result_b) == 1
        assert result_b[0]["evidence"]["content"] == "BTC: $65,000."
        assert ol.get_evidence("fp-nonexistent") == []

    def test_get_evidence_most_recent_first(self):
        ol.record_evidence("fp-multi", "online", "primera respuesta")
        ol.record_evidence("fp-multi", "online", "segunda respuesta (mas reciente)")
        rows = ol.get_evidence("fp-multi")
        assert len(rows) == 2
        assert rows[0]["evidence"]["content"] == "segunda respuesta (mas reciente)"

    def test_record_evidence_empty_fingerprint_skipped(self):
        row_id = ol.record_evidence("", "online", "contenido")
        assert row_id == -1
        assert ol.get_by_domain(ol.EVIDENCE_DOMAIN, limit=10) == []

    def test_record_evidence_non_global_scope_skipped(self):
        """RULE 8: sin isolation por usuario, scope!=GLOBAL nunca se escribe."""
        row_id = ol.record_evidence(
            "fp-personal", "model_inference", "dato personal del usuario",
            scope="USER",
        )
        assert row_id == -1
        assert ol.get_evidence("fp-personal") == []

    def test_record_evidence_model_inference_source_type_preserved(self):
        """RULE 5: COGNITIVE se marca source_type=model_inference y no se
        distingue especialmente en el store — el consumidor decide cómo
        tratarla, pero el tag debe sobrevivir intacto."""
        ol.record_evidence(
            "fp-cog", "model_inference", "respuesta generada por el LLM",
            source="openai", source_reference="gpt-4o-mini",
        )
        rows = ol.get_evidence("fp-cog")
        assert rows[0]["obs_type"] == "model_inference"
        assert rows[0]["evidence"]["source_type"] == "model_inference"
