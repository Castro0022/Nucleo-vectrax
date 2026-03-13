"""Test Audit Ledger: records, queries, diff hashing."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import core.audit_ledger as ledger


@pytest.fixture(autouse=True)
def use_temp_ledger(tmp_path, monkeypatch):
    """Use a temporary directory for the audit ledger during tests."""
    monkeypatch.setattr(ledger, "VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(ledger, "LEDGER_PATH", str(tmp_path / "test_ledger.db"))


def test_record_and_query():
    row_id = ledger.record(
        "proposal.apply",
        actor="owner",
        role="owner",
        diff_hash="abc123",
        decision="approved",
        reason="Test proposal",
    )
    assert row_id >= 1

    entries = ledger.query(limit=10)
    assert len(entries) >= 1
    latest = entries[0]
    assert latest["action"] == "proposal.apply"
    assert latest["actor"] == "owner"
    assert latest["diff_hash"] == "abc123"
    assert latest["decision"] == "approved"


def test_count():
    assert ledger.count() == 0

    ledger.record("test.action", actor="system")
    ledger.record("test.action", actor="system")

    assert ledger.count() == 2


def test_query_with_filter():
    ledger.record("proposal.apply", actor="owner")
    ledger.record("agent.register", actor="agent")

    proposals = ledger.query(action_filter="proposal.apply")
    agents = ledger.query(action_filter="agent.register")

    assert all(e["action"] == "proposal.apply" for e in proposals)
    assert all(e["action"] == "agent.register" for e in agents)


def test_compute_diff_hash():
    h1 = ledger.compute_diff_hash("some diff content")
    h2 = ledger.compute_diff_hash("some diff content")
    h3 = ledger.compute_diff_hash("different content")

    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 16  # truncated to 16 chars


def test_record_with_metadata():
    row_id = ledger.record(
        "config.change",
        actor="owner",
        metadata={"file": "config.yaml", "lines_changed": 5},
    )
    entries = ledger.query(limit=1)
    assert entries[0]["metadata"]  # should be a JSON string
