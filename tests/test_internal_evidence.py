"""
tests/test_internal_evidence.py — Contrato de la evidencia interna
===================================================================
Verifica la fachada de solo lectura `core.nucleus.internal_evidence`.

AISLAMIENTO (obligatorio): todas las pruebas usan `tmp_path`. Ninguna toca
`vault/`, `data/ideas.jsonl`, `audit_ledger.db` ni ninguna base real —
`InternalEvidence` acepta `reports_dir`/`ideas_path` inyectados exactamente
para esto. Las pruebas que ejercitan fuentes NO inyectables (censo, observer,
ledger) solo comprueban que la degradación es honesta, nunca escriben.

Lo que estas pruebas protegen es una propiedad, no una frase: que el Núcleo
NUNCA pueda afirmar un hecho que la fuente no contiene.
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
    resolve_access,
)

OWNER = EvidenceAccess(owner_canonical="mario", is_owner=True, owner_raw="mario")
STRANGER = EvidenceAccess(owner_canonical="ana", is_owner=False, owner_raw="tg:999")


# ---------------------------------------------------------------------------
# Fixtures aisladas
# ---------------------------------------------------------------------------

@pytest.fixture
def reports_dir(tmp_path: Path) -> Path:
    d = tmp_path / "audit_reports"
    d.mkdir()
    return d


def _write_report(directory: Path, name: str, *, age_s: float, problems: int) -> Path:
    import datetime as dt
    ts = dt.datetime.fromtimestamp(time.time() - age_s, dt.timezone.utc).isoformat()
    path = directory / name
    path.write_text(json.dumps({
        "mode": "full",
        "timestamp": ts,
        "severity": "warning" if problems else "ok",
        "problems": [{"check": f"c{i}", "detail": "x"} for i in range(problems)],
    }), encoding="utf-8")
    return path


@pytest.fixture
def ideas_path(tmp_path: Path) -> Path:
    return tmp_path / "ideas.jsonl"


def _write_idea(path: Path, idea_id: str, status: str = "pending", **over) -> None:
    import datetime as dt
    row = {
        "idea_id": idea_id,
        "title": "fallback resolvio mejor que la ruta primaria",
        "description": "resolve_local / resolve_identity",
        "source": "router_learning",
        "priority": "medium",
        "impact_score": 0.6,
        "convergence_level": 0.5,
        "priority_score": 0.22,
        "affected_component": "smart_router",
        "evidence": {"cases": 160},
        "status": status,
        "source_id": "router_1_fallback_resolved_1",
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    row.update(over)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


# ---------------------------------------------------------------------------
# Los cinco estados son distinguibles — el corazón del contrato
# ---------------------------------------------------------------------------

def test_empty_source_reports_empty_not_ok(reports_dir):
    """Una fuente vacía NUNCA produce una respuesta afirmativa."""
    ev = InternalEvidence(OWNER, reports_dir=str(reports_dir))
    result = ev.latest_diagnostic()
    assert result.status is EvidenceStatus.EMPTY
    assert result.items == []


def test_missing_source_dir_reports_empty(tmp_path):
    ev = InternalEvidence(OWNER, reports_dir=str(tmp_path / "no-existe"))
    assert ev.latest_diagnostic().status is EvidenceStatus.EMPTY


def test_fresh_diagnostic_is_ok_and_carries_source_and_age(reports_dir):
    _write_report(reports_dir, "audit_full_2026-09-22_100000.json", age_s=60, problems=2)
    result = InternalEvidence(OWNER, reports_dir=str(reports_dir)).latest_diagnostic()
    assert result.status is EvidenceStatus.OK
    item = result.items[0]
    assert item.source                      # fuente exacta, siempre
    assert item.age_seconds is not None     # antigüedad, siempre
    assert item.age_seconds < 3600
    assert item.data["problem_count"] == 2
    assert "2 problema" in item.summary


def test_old_diagnostic_is_stale_not_ok(reports_dir):
    """Evidencia vieja se marca STALE — no se presenta como vigente ni se oculta."""
    _write_report(reports_dir, "audit_full_2026-09-01_100000.json",
                  age_s=5 * 24 * 3600, problems=1)
    result = InternalEvidence(OWNER, reports_dir=str(reports_dir)).latest_diagnostic()
    assert result.status is EvidenceStatus.STALE
    assert result.items, "STALE conserva la evidencia, no la descarta"
    assert "desactualizada" in _answer(result).lower() or "vieja" in _answer(result).lower()


def test_unauthorized_is_distinct_from_empty(reports_dir):
    """'No estás autorizado' y 'no tengo evidencia' son respuestas distintas.

    Colapsarlas filtraría la existencia del dato o mentiría sobre ella.
    """
    _write_report(reports_dir, "audit_full_2026-09-22_100000.json", age_s=60, problems=3)
    denied = InternalEvidence(STRANGER, reports_dir=str(reports_dir)).latest_diagnostic()
    allowed = InternalEvidence(OWNER, reports_dir=str(reports_dir)).latest_diagnostic()
    assert denied.status is EvidenceStatus.UNAUTHORIZED
    assert allowed.status is EvidenceStatus.OK
    assert denied.items == [], "un no-owner no recibe NI UN item"


def test_unavailable_when_source_raises(monkeypatch, reports_dir):
    """Una fuente caída degrada a UNAVAILABLE con el motivo real."""
    ev = InternalEvidence(OWNER, reports_dir=str(reports_dir))
    monkeypatch.setattr(
        ev, "_read_reports",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disco no montado")),
    )
    result = ev.latest_diagnostic()
    assert result.status is EvidenceStatus.UNAVAILABLE
    assert "disco no montado" in result.detail


# ---------------------------------------------------------------------------
# Propuestas: `pending` se reporta como `pending`
# ---------------------------------------------------------------------------

def test_pending_proposal_keeps_pending_status(ideas_path):
    _write_idea(ideas_path, "IDEA-AAAA1111", status="pending")
    result = InternalEvidence(OWNER, ideas_path=str(ideas_path)).pending_proposals()
    assert result.status is EvidenceStatus.OK
    assert result.items[0].status == "pending"
    assert result.items[0].reference == "IDEA-AAAA1111"


def test_approved_proposal_is_not_reported_as_executed(ideas_path):
    """Una propuesta aprobada NO se convierte en 'reparación ejecutada'."""
    _write_idea(ideas_path, "IDEA-BBBB2222", status="approved")
    result = InternalEvidence(OWNER, ideas_path=str(ideas_path)).proposal_by_id("IDEA-BBBB2222")
    item = result.items[0]
    assert item.status == "approved"
    assert item.data["applied_at"] is None
    text = _answer(result).lower()
    for forbidden in ("ejecutad", "reparad", "resuelto", "arreglad"):
        assert forbidden not in text, f"la respuesta insinúa ejecución: {text!r}"


def test_unknown_proposal_id_is_empty_not_invented(ideas_path):
    _write_idea(ideas_path, "IDEA-AAAA1111")
    result = InternalEvidence(OWNER, ideas_path=str(ideas_path)).proposal_by_id("IDEA-NOEXISTE")
    assert result.status is EvidenceStatus.EMPTY
    assert result.items == []


def test_proposals_are_owner_only(ideas_path):
    _write_idea(ideas_path, "IDEA-AAAA1111")
    result = InternalEvidence(STRANGER, ideas_path=str(ideas_path)).pending_proposals()
    assert result.status is EvidenceStatus.UNAUTHORIZED
    assert result.items == []


def test_pending_total_is_reported_even_when_list_is_truncated(ideas_path):
    for n in range(12):
        _write_idea(ideas_path, f"IDEA-CCCC{n:04d}")
    result = InternalEvidence(OWNER, ideas_path=str(ideas_path)).pending_proposals(limit=3)
    assert len(result.items) == 3
    assert result.items[0].data["pending_total"] == 12, "el total real no se pierde"


# ---------------------------------------------------------------------------
# Autorización desde identidad canónica, nunca desde el canal
# ---------------------------------------------------------------------------

def test_access_is_fail_closed_for_unknown_identity():
    assert resolve_access("").is_owner is False
    assert resolve_access("desconocido").is_owner is False


def test_public_kinds_readable_without_owner():
    """Los totales del universo no son secretos: un usuario común puede verlos."""
    assert STRANGER.may_read("universe") is True
    assert STRANGER.may_read("stars") is True


def test_sensitive_kinds_require_owner():
    for kind in ("diagnostic", "proposal", "audit", "engines", "operational",
                 "approval_pipeline"):
        assert STRANGER.may_read(kind) is False, f"{kind} no debe ser público"
        assert OWNER.may_read(kind) is True


# ---------------------------------------------------------------------------
# Botón Aprobar: traza honesta
# ---------------------------------------------------------------------------

def test_approval_pipeline_reports_executor_absence_honestly():
    """La traza debe decir si existe o no un ejecutor posterior — sin simular."""
    result = InternalEvidence(OWNER).approval_pipeline()
    assert result.status in (EvidenceStatus.OK, EvidenceStatus.UNAVAILABLE)
    if result.status is EvidenceStatus.UNAVAILABLE:
        pytest.skip("traza estructural no computable en este entorno")
    executor = [i for i in result.items if i.scope.endswith("ejecutor")]
    assert executor, "la traza debe incluir el paso del ejecutor"
    # El estado se DERIVA del código real, no se afirma a ciegas.
    assert executor[0].status in ("present", "absent")
    if executor[0].status == "absent":
        assert "AUSENTE" in executor[0].summary


def test_approval_pipeline_keeps_the_two_circuits_separate():
    """`ideas` y `proposals` son sistemas distintos. Encadenarlos describiría
    un flujo que no existe — el defecto exacto que esta etapa corrige."""
    result = InternalEvidence(OWNER).approval_pipeline()
    if result.status is EvidenceStatus.UNAVAILABLE:
        pytest.skip("traza estructural no computable en este entorno")

    scopes = [i.scope for i in result.items]
    assert any(s.startswith("ideas/") for s in scopes)
    assert any(s.startswith("proposals/") for s in scopes)

    # Cada circuito nombra su propio endpoint y su propio almacén.
    ideas = {i.scope: i for i in result.items if i.scope.startswith("ideas/")}
    props = {i.scope: i for i in result.items if i.scope.startswith("proposals/")}
    assert "/v1/ideas/" in ideas["ideas/1.endpoint"].summary
    assert "ideas.jsonl" in ideas["ideas/2.persistencia"].summary
    assert "/v1/proposals/" in props["proposals/1.endpoint"].summary
    assert "vectrax.db" in props["proposals/2.persistencia"].summary

    # La auditoría de `proposals` NO puede atribuirse al circuito de `ideas`.
    assert ideas["ideas/3.auditoria"].status == "absent"
    assert "NO escribe" in ideas["ideas/3.auditoria"].summary

    # Y la relación entre ambos se afirma explícitamente.
    rel = [i for i in result.items if i.scope == "relacion"]
    assert rel and rel[0].status == "independent"


def test_approval_pipeline_does_not_count_itself_as_executor():
    """Regresión: buscar el nombre `mark_applied` en vez de la LLAMADA hacía
    que este propio módulo se contara como ejecutor, produciendo un
    'PRESENTE' falso."""
    result = InternalEvidence(OWNER).approval_pipeline()
    if result.status is EvidenceStatus.UNAVAILABLE:
        pytest.skip("traza estructural no computable en este entorno")
    executor = [i for i in result.items if i.scope.endswith("ejecutor")][0]
    assert "internal_evidence" not in executor.summary, (
        "el escáner se contó a sí mismo como ejecutor"
    )


def test_approval_pipeline_is_owner_only():
    assert InternalEvidence(STRANGER).approval_pipeline().status is EvidenceStatus.UNAUTHORIZED


# ---------------------------------------------------------------------------
# Solo lectura
# ---------------------------------------------------------------------------

def test_reading_does_not_mutate_the_fixture(reports_dir, ideas_path):
    _write_report(reports_dir, "audit_full_2026-09-22_100000.json", age_s=60, problems=1)
    _write_idea(ideas_path, "IDEA-AAAA1111")
    before = {p.name: p.stat().st_mtime_ns for p in reports_dir.iterdir()}
    ideas_before = ideas_path.read_bytes()

    ev = InternalEvidence(OWNER, reports_dir=str(reports_dir), ideas_path=str(ideas_path))
    ev.latest_diagnostic()
    ev.diagnostic_history(limit=5)
    ev.pending_proposals()
    ev.proposal_by_id("IDEA-AAAA1111")

    after = {p.name: p.stat().st_mtime_ns for p in reports_dir.iterdir()}
    assert before == after, "la lectura de diagnósticos modificó el directorio"
    assert ideas_path.read_bytes() == ideas_before, "la lectura mutó ideas.jsonl"


def test_module_contains_no_write_operations():
    """Invariante estructural: el módulo no abre nada en escritura."""
    src = (_ROOT / "core" / "nucleus" / "internal_evidence.py").read_text(encoding="utf-8")
    for forbidden in ('"w"', "'w'", '"a"', "'a'", "INSERT ", "UPDATE ", "DELETE ",
                      "os.remove", "unlink", "makedirs", "rmtree"):
        assert forbidden not in src, f"operación de escritura detectada: {forbidden}"


# ---------------------------------------------------------------------------
# Fuentes reales no inyectables: degradación honesta, nunca invención
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method", [
    "recent_audit", "engine_status", "universe_summary",
    "stars", "patterns", "domains", "convergences",
])
def test_real_sources_never_raise_and_never_invent(method):
    result = getattr(InternalEvidence(OWNER), method)()
    assert result.status in set(EvidenceStatus)
    if result.status is not EvidenceStatus.OK:
        assert result.items == [] or result.status is EvidenceStatus.STALE
    for item in result.items:
        assert item.source, "todo item declara su fuente"


@pytest.mark.parametrize("domain", ["trading", "freight_logistics"])
def test_operational_domains_degrade_honestly(domain):
    result = InternalEvidence(OWNER).operational_activity(domain)
    assert result.status in set(EvidenceStatus)
    for item in result.items:
        assert item.source and item.scope


def _answer(result) -> str:
    from core.nucleus.evidence_intent import build_answer
    return build_answer(result)
