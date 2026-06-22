"""
Wiring test (Pilar D): a real pipeline_worker runtime fault must become a
VISIBLE quality phenomenon (failure star) in the Universe.

Validates the producer we connected: `_observe_runtime_error` →
`quality_observer.record_runtime_error` → observation_ledger → `quality_entities`.

Hermetic: the autouse conftest baseline redirects the observation ledger to a
per-test temp vault, so these records never touch the real vault.
"""
from __future__ import annotations

import pytest

from core.transport import pipeline_worker as pw
from core.self_observation.quality_entities import get_quality_entities


def test_runtime_fault_becomes_failure_entity(monkeypatch):
    monkeypatch.setenv("VX_RUNTIME_OBSERVER", "1")

    before = get_quality_entities()["counts"]["failures"]
    pw._observe_runtime_error(ValueError("boom"), where="pipeline_worker")
    after = get_quality_entities()

    assert after["counts"]["failures"] >= before + 1
    # The new failure star is anchored to the worker component.
    assert any(f.get("module") == "pipeline_worker" for f in after["failures"])


def test_timeout_fault_anchors_to_timeout_component(monkeypatch):
    monkeypatch.setenv("VX_RUNTIME_OBSERVER", "1")

    pw._observe_runtime_error(
        TimeoutError("message exceeded 45s"), where="pipeline_worker.timeout",
    )
    ents = get_quality_entities()
    assert any(
        f.get("module") == "pipeline_worker.timeout" for f in ents["failures"]
    )


def test_disabled_flag_records_nothing(monkeypatch):
    monkeypatch.setenv("VX_RUNTIME_OBSERVER", "0")

    before = get_quality_entities()["counts"]["failures"]
    pw._observe_runtime_error(ValueError("nope"), where="pipeline_worker")
    after = get_quality_entities()["counts"]["failures"]

    assert after == before  # off-switch honored → no phenomenon recorded


def test_observe_runtime_error_never_raises(monkeypatch):
    """Defensive contract: the observer must never propagate, even if the
    quality_observer import/record fails."""
    monkeypatch.setenv("VX_RUNTIME_OBSERVER", "1")

    def _boom(*a, **k):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(
        "core.self_observation.quality_observer.record_runtime_error", _boom,
    )
    # Must not raise despite the producer blowing up.
    pw._observe_runtime_error(ValueError("x"), where="pipeline_worker")
