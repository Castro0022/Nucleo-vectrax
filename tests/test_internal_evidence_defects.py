"""
tests/test_internal_evidence_defects.py — Regresiones de tres defectos reales.

Cada clase corresponde a un defecto DEMOSTRADO en la auditoría del 2026-09-22
sobre `core/nucleus/internal_evidence.py`:

  1. `_domain_activity()` leía `total_patterns`, clave que
     `core/domain_knowledge.py::get_domain_summary()` NUNCA produce (devuelve
     `patterns`). El Núcleo reportaba "0 patrón(es)" con la librería llena.

  2. `_read_reports()` ordenaba por NOMBRE de archivo. Como el nombre es
     `audit_{mode}_{ts}.json`, en orden alfabético descendente cualquier
     `audit_weekly_*` ganaba a cualquier `audit_daily_*`, por vieja que fuera
     la semanal: "el último diagnóstico" podía tener días.

  3. `recent_audit()` descartaba `metadata` entero e ignoraba el
     `action_filter` que `core/audit_ledger.py::query()` sí soporta, así que
     el resumen de los ciclos de dominio nunca llegaba al Núcleo.

AISLAMIENTO: todo con `tmp_path` y dobles inyectados. Ninguna prueba toca
`vault/`, `audit_ledger.db` ni la librería de dominio real.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.nucleus.internal_evidence import (  # noqa: E402
    EvidenceAccess,
    EvidenceStatus,
    InternalEvidence,
)

OWNER = EvidenceAccess(owner_canonical="mario", is_owner=True, owner_raw="mario")
STRANGER = EvidenceAccess(owner_canonical="ana", is_owner=False, owner_raw="tg:999")


# ===========================================================================
# DEFECTO 1 — conteo de patrones de dominio
# ===========================================================================

class TestDomainPatternCount:

    @staticmethod
    def _patch_summary(monkeypatch, value):
        """Sustituye `get_domain_summary` en el módulo dueño del dato.

        `_domain_activity()` lo importa dentro de la función, así que el
        parcheo tiene que ir sobre `core.domain_knowledge`.
        """
        import core.domain_knowledge as dk
        monkeypatch.setattr(dk, "get_domain_summary", lambda domain: value)

    def test_real_contract_key_is_reflected_exactly(self, monkeypatch):
        """`patterns > 0` se refleja tal cual, no como cero."""
        self._patch_summary(monkeypatch, {
            "domain": "freight_logistics",
            "patterns": 7,
            "strong_patterns": 3,
            "avg_win_rate": 61.0,
            "total_observations": 240,
        })
        res = InternalEvidence(OWNER).operational_activity("freight_logistics")
        assert res.status is EvidenceStatus.OK
        assert "7 patrón(es)" in res.items[0].summary
        # El resumen completo de la fuente sigue viajando en `data`.
        assert res.items[0].data["strong_patterns"] == 3
        assert res.items[0].data["patterns"] == 7

    def test_zero_patterns_is_an_honest_empty(self, monkeypatch):
        """La rama sin patrones de la fuente es `{"domain": d, "patterns": 0}`."""
        self._patch_summary(monkeypatch, {"domain": "freight_logistics", "patterns": 0})
        res = InternalEvidence(OWNER).operational_activity("freight_logistics")
        assert res.status is EvidenceStatus.EMPTY
        assert res.items == []
        assert "sin patrones registrados" in res.detail

    def test_missing_contract_key_is_unavailable_not_zero(self, monkeypatch):
        """El defecto original: clave ausente NO puede presentarse como cero."""
        self._patch_summary(monkeypatch, {"domain": "freight_logistics", "total_patterns": 12})
        res = InternalEvidence(OWNER).operational_activity("freight_logistics")
        assert res.status is EvidenceStatus.UNAVAILABLE
        assert "patterns" in res.detail
        assert res.items == []
        # Y en ningún caso afirma un número.
        assert "0 patrón" not in res.detail

    def test_non_integer_count_is_unavailable(self, monkeypatch):
        self._patch_summary(monkeypatch, {"domain": "x", "patterns": "muchos"})
        res = InternalEvidence(OWNER).operational_activity("x")
        assert res.status is EvidenceStatus.UNAVAILABLE
        assert "no es un entero" in res.detail

    def test_source_is_preserved(self, monkeypatch):
        self._patch_summary(monkeypatch, {"domain": "freight_logistics", "patterns": 2})
        res = InternalEvidence(OWNER).operational_activity("freight_logistics")
        assert "core.domain_knowledge.get_domain_summary" in res.source
        assert res.items[0].reference == "domain_knowledge.freight_logistics"
        assert res.items[0].status == "observed"

    def test_age_is_none_when_source_does_not_date_it(self, monkeypatch):
        """La fuente no fecha el resumen: `None`, nunca `now()` ni 0 segundos."""
        self._patch_summary(monkeypatch, {"domain": "freight_logistics", "patterns": 2})
        res = InternalEvidence(OWNER).operational_activity("freight_logistics")
        assert res.items[0].age_seconds is None

    @pytest.mark.parametrize(
        "domain", ["freight_logistics", "cybersecurity", "florida_real_estate", "restaurant"],
    )
    def test_same_implementation_for_every_generic_domain(self, monkeypatch, domain):
        """Freight no tiene camino propio: todo dominio genérico usa esta ruta."""
        self._patch_summary(monkeypatch, {"domain": domain, "patterns": 5})
        res = InternalEvidence(OWNER).operational_activity(domain)
        assert res.status is EvidenceStatus.OK
        assert res.items[0].scope == domain
        assert f"{domain}: 5 patrón(es)" in res.items[0].summary

    def test_unauthorized_before_touching_the_source(self, monkeypatch):
        def _boom(domain):
            raise AssertionError("no debe consultarse la fuente sin autorización")
        import core.domain_knowledge as dk
        monkeypatch.setattr(dk, "get_domain_summary", _boom)
        res = InternalEvidence(STRANGER).operational_activity("freight_logistics")
        assert res.status is EvidenceStatus.UNAUTHORIZED


# ===========================================================================
# DEFECTO 2 — selección del último diagnóstico
# ===========================================================================

def _report(directory: Path, name: str, *, ts: float | None, mode: str,
            problems: int = 0, mtime: float | None = None) -> Path:
    """Escribe un reporte. `ts=None` omite la marca de tiempo del contenido."""
    import datetime as dt
    body = {
        "mode": mode,
        "severity": "warning" if problems else "ok",
        "problems": [{"check": f"c{i}"} for i in range(problems)],
    }
    if ts is not None:
        body["timestamp"] = dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat()
    path = directory / name
    path.write_text(json.dumps(body), encoding="utf-8")
    if mtime is not None:
        import os
        os.utime(path, (mtime, mtime))
    return path


class TestDiagnosticOrdering:

    @pytest.fixture
    def reports(self, tmp_path: Path) -> Path:
        d = tmp_path / "audit_reports"
        d.mkdir()
        return d

    def _evidence(self, reports: Path) -> InternalEvidence:
        return InternalEvidence(OWNER, reports_dir=str(reports))

    def test_newer_daily_beats_older_weekly(self, reports):
        """El defecto original: 'weekly' > 'daily' alfabéticamente."""
        now = time.time()
        _report(reports, "audit_weekly_2026-09-14_010000.json", ts=now - 3 * 86400, mode="weekly")
        _report(reports, "audit_daily_2026-09-22_010000.json", ts=now - 600, mode="daily")
        res = self._evidence(reports).latest_diagnostic()
        assert res.status is EvidenceStatus.OK
        assert res.items[0].reference == "audit_daily_2026-09-22_010000.json"
        assert res.items[0].scope == "daily"

    def test_newer_weekly_beats_older_daily(self, reports):
        """La corrección no invierte el sesgo: gana el más reciente, sin más."""
        now = time.time()
        _report(reports, "audit_daily_2026-09-01_010000.json", ts=now - 5 * 86400, mode="daily")
        _report(reports, "audit_weekly_2026-09-22_010000.json", ts=now - 600, mode="weekly")
        res = self._evidence(reports).latest_diagnostic()
        assert res.items[0].reference == "audit_weekly_2026-09-22_010000.json"
        assert res.items[0].scope == "weekly"

    def test_misleading_filenames_do_not_change_the_result(self, reports):
        """Nombre que sugiere lo contrario del contenido: manda el contenido."""
        now = time.time()
        _report(reports, "audit_zzz_9999-12-31_235959.json", ts=now - 10 * 86400, mode="daily")
        _report(reports, "audit_aaa_1970-01-01_000000.json", ts=now - 60, mode="daily")
        res = self._evidence(reports).latest_diagnostic()
        assert res.items[0].reference == "audit_aaa_1970-01-01_000000.json"

    def test_invalid_timestamp_falls_back_to_mtime(self, reports):
        now = time.time()
        _report(reports, "audit_daily_ok.json", ts=now - 5 * 86400, mode="daily")
        bad = _report(reports, "audit_daily_bad.json", ts=None, mode="daily", mtime=now - 30)
        bad_body = json.loads(bad.read_text(encoding="utf-8"))
        assert "timestamp" not in bad_body
        res = self._evidence(reports).latest_diagnostic()
        assert res.items[0].reference == "audit_daily_bad.json"
        assert res.items[0].data["observed_at_source"] == "file.mtime"

    def test_valid_timestamp_is_labelled_as_such(self, reports):
        _report(reports, "audit_daily_x.json", ts=time.time() - 60, mode="daily")
        res = self._evidence(reports).latest_diagnostic()
        assert res.items[0].data["observed_at_source"] == "report.timestamp"

    def test_tie_is_deterministic(self, reports):
        """Misma fecha exacta: desempate estable por nombre descendente."""
        ts = time.time() - 300
        _report(reports, "audit_daily_aaa.json", ts=ts, mode="daily")
        _report(reports, "audit_daily_bbb.json", ts=ts, mode="daily")
        first = self._evidence(reports).latest_diagnostic().items[0].reference
        for _ in range(5):
            assert self._evidence(reports).latest_diagnostic().items[0].reference == first
        assert first == "audit_daily_bbb.json"

    def test_corrupt_report_does_not_break_the_query_and_is_named(self, reports):
        now = time.time()
        _report(reports, "audit_daily_good.json", ts=now - 120, mode="daily")
        (reports / "audit_daily_corrupt.json").write_text("{no es json", encoding="utf-8")
        res = self._evidence(reports).latest_diagnostic()
        assert res.status is EvidenceStatus.OK
        assert res.items[0].reference == "audit_daily_good.json"
        assert "audit_daily_corrupt.json" in res.detail
        assert "ilegible" in res.detail

    def test_json_that_is_not_an_object_is_treated_as_unreadable(self, reports):
        _report(reports, "audit_daily_good.json", ts=time.time() - 120, mode="daily")
        (reports / "audit_daily_list.json").write_text("[1, 2, 3]", encoding="utf-8")
        res = self._evidence(reports).latest_diagnostic()
        assert res.items[0].reference == "audit_daily_good.json"
        assert "audit_daily_list.json" in res.detail

    def test_history_is_ordered_newest_first(self, reports):
        now = time.time()
        _report(reports, "audit_weekly_old.json", ts=now - 4 * 86400, mode="weekly")
        _report(reports, "audit_daily_mid.json", ts=now - 2 * 86400, mode="daily")
        _report(reports, "audit_daily_new.json", ts=now - 3600, mode="daily")
        res = InternalEvidence(OWNER, reports_dir=str(reports)).diagnostic_history(limit=3)
        assert [i.reference for i in res.items] == [
            "audit_daily_new.json", "audit_daily_mid.json", "audit_weekly_old.json",
        ]

    def test_stale_detection_is_preserved(self, reports):
        """La ventana de `diagnostic` son 24 h: un reporte más viejo es STALE."""
        _report(reports, "audit_daily_old.json", ts=time.time() - 3 * 86400, mode="daily")
        res = self._evidence(reports).latest_diagnostic()
        assert res.status is EvidenceStatus.STALE
        assert res.items[0].reference == "audit_daily_old.json"

    def test_mode_travels_as_data_not_as_ordering(self, reports):
        _report(reports, "audit_weekly_x.json", ts=time.time() - 60, mode="weekly")
        res = self._evidence(reports).latest_diagnostic()
        assert res.items[0].data["mode"] == "weekly"
        assert res.items[0].scope == "weekly"


# ===========================================================================
# DEFECTO 3 — auditoría: filtro por acción y detalles seguros
# ===========================================================================

def _row(idx: int, action: str, *, metadata=None, actor="operator",
         decision="approved", reason="", ts=None):
    import datetime as dt
    if ts is None:
        ts = dt.datetime.now(dt.timezone.utc).isoformat()
    return {
        "id": idx,
        "timestamp": ts,
        "actor": actor,
        "role": "owner",
        "action": action,
        "diff_hash": "",
        "decision": decision,
        "reason": reason,
        # La columna real de SQLite es TEXT: `query()` devuelve una cadena.
        "metadata": json.dumps(metadata) if metadata is not None else "{}",
    }


class _FakeLedger:
    """Doble de `core.audit_ledger` con la MISMA firma de `query()`."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def query(self, limit=100, action_filter=None):
        self.calls.append({"limit": limit, "action_filter": action_filter})
        rows = self.rows
        if action_filter:
            rows = [r for r in rows if r["action"] == action_filter]
        return rows[:limit]


@pytest.fixture
def fake_ledger(monkeypatch):
    """Inyecta el doble donde `recent_audit()` hace su import diferido."""
    import core.audit_ledger as real

    def _install(rows):
        fake = _FakeLedger(rows)
        monkeypatch.setattr(real, "query", fake.query)
        return fake

    return _install


class TestAuditActionFilter:

    def test_filter_returns_only_the_requested_action(self, fake_ledger):
        fake = fake_ledger([
            _row(1, "freight_learning_cycle"),
            _row(2, "cyber_learning_cycle"),
            _row(3, "freight_learning_cycle"),
        ])
        res = InternalEvidence(OWNER).recent_audit(action="freight_learning_cycle")
        assert {i.summary for i in res.items} == {"freight_learning_cycle"}
        assert len(res.items) == 2
        assert fake.calls[-1]["action_filter"] == "freight_learning_cycle"
        assert "freight_learning_cycle" in res.source

    def test_without_filter_behaviour_is_unchanged(self, fake_ledger):
        fake = fake_ledger([_row(1, "a"), _row(2, "b")])
        res = InternalEvidence(OWNER).recent_audit()
        assert len(res.items) == 2
        assert fake.calls[-1]["action_filter"] is None
        assert res.items[0].scope == "operator"        # actor
        assert res.items[0].status == "approved"       # decision
        assert res.items[0].reference == "audit:1"

    def test_unknown_action_is_an_honest_empty(self, fake_ledger):
        fake_ledger([_row(1, "freight_learning_cycle")])
        res = InternalEvidence(OWNER).recent_audit(action="no_existe")
        assert res.status is EvidenceStatus.EMPTY
        assert "no_existe" in res.detail


class TestAuditSafeDetails:

    def test_known_structured_details_reach_the_result(self, fake_ledger):
        fake_ledger([_row(1, "freight_learning_cycle", metadata={
            "category": "operator.memory",
            "risk_zone": "green",
            "details": {
                "success": True,
                "provider": "simulator",
                "events_ingested": 200,
                "verified_decisive": 12,
                "verified_win_rate": 58.3,
            },
        })])
        item = InternalEvidence(OWNER).recent_audit().items[0]
        d = item.data["details"]
        assert d["success"] is True
        assert d["provider"] == "simulator"
        assert d["events_ingested"] == 200
        assert d["verified_decisive"] == 12
        assert d["verified_win_rate"] == 58.3
        # Dominio derivado del emisor, y alcance derivado del proveedor.
        assert item.data["domain"] == "freight_logistics"
        assert item.data["data_scope"] == "simulated"
        # Campos base intactos.
        assert item.data["role"] == "owner"
        assert item.observed_at > 0

    def test_real_provider_is_scoped_as_real(self, fake_ledger):
        fake_ledger([_row(1, "cyber_learning_cycle", metadata={
            "details": {"provider": "nvd", "events": 500, "wins": 3},
        })])
        item = InternalEvidence(OWNER).recent_audit().items[0]
        assert item.data["data_scope"] == "real"
        assert item.data["domain"] == "cybersecurity"

    def test_unknown_provider_is_not_guessed(self, fake_ledger):
        fake_ledger([_row(1, "cyber_learning_cycle", metadata={
            "details": {"provider": "otracosa", "events": 1},
        })])
        item = InternalEvidence(OWNER).recent_audit().items[0]
        assert "data_scope" not in item.data
        assert item.data["details"]["provider"] == "otracosa"

    def test_unknown_action_exposes_no_details(self, fake_ledger):
        fake_ledger([_row(1, "accion_desconocida", metadata={
            "details": {"secreto": "valor", "events_ingested": 5},
        })])
        item = InternalEvidence(OWNER).recent_audit().items[0]
        assert "details" not in item.data
        assert "domain" not in item.data

    def test_unallowlisted_keys_are_dropped(self, fake_ledger):
        fake_ledger([_row(1, "freight_learning_cycle", metadata={
            "details": {
                "events_ingested": 10,
                "tenant_id": "tenant-abc123",
                "api_key": "sk-secreto",
                "path": "/Users/alguien/.env",
            },
        })])
        d = InternalEvidence(OWNER).recent_audit().items[0].data["details"]
        assert d == {"events_ingested": 10}

    def test_full_metadata_object_is_never_exposed(self, fake_ledger):
        fake_ledger([_row(1, "freight_learning_cycle", metadata={
            "category": "operator.memory",
            "risk_zone": "green",
            "operator_timestamp": "2026-09-22T00:00:00+00:00",
            "details": {"events_ingested": 3},
        })])
        data = InternalEvidence(OWNER).recent_audit().items[0].data
        assert "metadata" not in data
        assert "category" not in data
        assert "risk_zone" not in data
        assert "operator_timestamp" not in data

    def test_free_text_is_rejected(self, fake_ledger):
        """`detail` de los ciclos es `str(exc)[:200]`: puede traer rutas."""
        fake_ledger([_row(1, "freight_learning_cycle", metadata={
            "details": {
                "success": False,
                "detail": "FileNotFoundError: /Users/mariobravo/.vectrax/x.json",
            },
        })])
        d = InternalEvidence(OWNER).recent_audit().items[0].data["details"]
        assert d == {"success": False}
        assert "detail" not in d

    def test_oversized_string_is_rejected(self, fake_ledger):
        fake_ledger([_row(1, "freight_learning_cycle", metadata={
            "details": {"provider": "x" * 500, "events_ingested": 1},
        })])
        d = InternalEvidence(OWNER).recent_audit().items[0].data["details"]
        assert "provider" not in d
        assert d["events_ingested"] == 1

    def test_nested_structures_are_dropped(self, fake_ledger):
        fake_ledger([_row(1, "freight_learning_cycle", metadata={
            "details": {
                "events_ingested": 2,
                "errors": {"anidado": {"mas": "hondo"}},
            },
        })])
        d = InternalEvidence(OWNER).recent_audit().items[0].data["details"]
        assert d == {"events_ingested": 2}

    def test_long_lists_are_truncated(self, fake_ledger):
        fake_ledger([_row(1, "trading_convergence_learner", metadata={
            "details": {"drift_kinds": [f"k{i}" for i in range(50)]},
        })])
        d = InternalEvidence(OWNER).recent_audit().items[0].data["details"]
        assert len(d["drift_kinds"]) == 8

    def test_missing_metadata_is_backward_compatible(self, fake_ledger):
        fake_ledger([_row(1, "freight_learning_cycle", metadata=None)])
        item = InternalEvidence(OWNER).recent_audit().items[0]
        assert "details" not in item.data
        assert item.data["role"] == "owner"
        assert item.summary == "freight_learning_cycle"

    def test_unparseable_metadata_does_not_break(self, fake_ledger):
        rows = [_row(1, "freight_learning_cycle")]
        rows[0]["metadata"] = "{esto no es json"
        fake_ledger(rows)
        item = InternalEvidence(OWNER).recent_audit().items[0]
        assert "details" not in item.data
        assert item.summary == "freight_learning_cycle"


class TestAuditAuthorization:

    def test_stranger_gets_neither_counts_nor_details(self, fake_ledger):
        fake_ledger([_row(1, "freight_learning_cycle", metadata={
            "details": {"events_ingested": 999},
        })])
        res = InternalEvidence(STRANGER).recent_audit()
        assert res.status is EvidenceStatus.UNAUTHORIZED
        assert res.items == []
        assert "999" not in json.dumps(res.to_dict())

    def test_stranger_cannot_bypass_with_a_filter(self, fake_ledger):
        fake_ledger([_row(1, "freight_learning_cycle")])
        res = InternalEvidence(STRANGER).recent_audit(action="freight_learning_cycle")
        assert res.status is EvidenceStatus.UNAUTHORIZED
        assert res.items == []
