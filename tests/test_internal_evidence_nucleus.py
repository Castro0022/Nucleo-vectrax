"""
tests/test_internal_evidence_nucleus.py — El Núcleo responde con evidencia real
================================================================================
Cierra la etapa de reunificación del Núcleo con su observación interna.

Tres bloques:

1. ANTI-ALUCINACIÓN — el Núcleo no puede inventar un diagnóstico, no puede
   convertir `pending` en resuelto, no puede presentar una aprobación de
   gobernanza como una reparación ejecutada, no puede dar cifras sin fuente y
   no puede responder afirmativamente desde una fuente vacía.

2. CONSISTENCIA MULTICANAL — las mismas preguntas por los entry points reales
   (`resolve()` para web y CLI, `resolve_from_record()` para Telegram)
   producen los MISMOS hechos. No se exige redacción idéntica.

3. NÚCLEO ÚNICO — prueba estructural sobre los archivos reales de los tres
   adaptadores: todos delegan en `NucleusAuthority` y ninguno mantiene lógica
   cognitiva paralela.

SOBRE LOS MOCKS (honestidad de alcance): el ÚNICO elemento sustituido es la
FUENTE DE DATOS (`internal_evidence.get_internal_evidence`), y se sustituye
por una fachada real apuntando a fixtures en `tmp_path`. Todo el recorrido
—resolución de identidad canónica, consulta incondicional de memoria,
clasificación de intención, autorización, redacción determinista y adaptación
de canal— se ejecuta de verdad en cada prueba. No hay ningún mock de
`NucleusAuthority`, de los adaptadores ni del clasificador.

AISLAMIENTO DE BASE: se copia `~/.vectrax/vectrax.db` a un directorio temporal
y se hace monkeypatch de `DB_PATH` (mismo patrón que
`tests/test_nucleus_reunification.py`). Ninguna prueba escribe en producción.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.nucleus import internal_evidence as _ie  # noqa: E402
from core.nucleus.internal_evidence import (  # noqa: E402
    EvidenceAccess, EvidenceStatus, InternalEvidence,
)
from core.nucleus.nucleus_authority import get_nucleus_authority  # noqa: E402


# ---------------------------------------------------------------------------
# Aislamiento de la base real
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def isolated_vectrax_db():
    real_db = Path.home() / ".vectrax" / "vectrax.db"
    tmp_dir = Path(tempfile.mkdtemp(prefix="vectrax_evidence_test_"))
    tmp_db = tmp_dir / "vectrax.db"
    if real_db.exists():
        shutil.copy2(real_db, tmp_db)
        for ext in ("-wal", "-shm"):
            src = Path(str(real_db) + ext)
            if src.exists():
                shutil.copy2(src, Path(str(tmp_db) + ext))

    mp = pytest.MonkeyPatch()
    import vectrax.db as _db_mod
    import vectrax.identity_aliases as _alias_mod
    mp.setattr(_db_mod, "DB_DIR", tmp_dir, raising=True)
    mp.setattr(_db_mod, "DB_PATH", tmp_db, raising=True)
    mp.setattr(_alias_mod, "DB_PATH", tmp_db, raising=True)
    _db_mod.init_db()
    yield tmp_db
    mp.undo()
    shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Fixtures de evidencia (datos falsos, recorrido real)
# ---------------------------------------------------------------------------

@pytest.fixture
def evidence_fixture(tmp_path, monkeypatch):
    """Sustituye SOLO la fuente de datos por una fachada real sobre fixtures."""
    reports = tmp_path / "audit_reports"
    reports.mkdir()
    ideas = tmp_path / "ideas.jsonl"

    def _factory(owner: str, owner_raw: str = "") -> InternalEvidence:
        return InternalEvidence(
            _ie.resolve_access(owner, owner_raw=owner_raw),
            reports_dir=str(reports), ideas_path=str(ideas),
        )

    monkeypatch.setattr(_ie, "get_internal_evidence", _factory)
    return {"reports": reports, "ideas": ideas}


def _seed_diagnostic(reports: Path, *, problems: int = 3, age_s: float = 120) -> None:
    import datetime as dt
    ts = dt.datetime.fromtimestamp(time.time() - age_s, dt.timezone.utc).isoformat()
    (reports / "audit_full_2026-09-22_120000.json").write_text(json.dumps({
        "mode": "full", "timestamp": ts, "severity": "warning",
        "problems": [{"check": f"check_{i}"} for i in range(problems)],
    }), encoding="utf-8")


def _seed_proposal(ideas: Path, idea_id: str, status: str = "pending") -> None:
    import datetime as dt
    with open(ideas, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "idea_id": idea_id,
            "title": "fallback resolvio mejor que la ruta primaria",
            "description": "resolve_local / resolve_identity",
            "source": "router_learning", "priority": "medium",
            "impact_score": 0.6, "convergence_level": 0.5, "priority_score": 0.22,
            "affected_component": "smart_router", "evidence": {"cases": 160},
            "status": status, "source_id": "router_1_fallback_resolved_1",
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }) + "\n")


def _ask(text: str, *, source: str = "api", owner: str = "mario"):
    """Entry point real de web/CLI."""
    return get_nucleus_authority().resolve(text, channel="creator", owner=owner, source=source)


def _ask_telegram(text: str, owner: str = "mario"):
    """Entry point real de Telegram (`pipeline_worker.py` usa este método)."""
    return get_nucleus_authority().resolve_from_record(
        text, channel="creator", owner=owner, source="telegram", record=None,
    )


# ---------------------------------------------------------------------------
# 1. ANTI-ALUCINACIÓN
# ---------------------------------------------------------------------------

def test_empty_source_never_produces_an_affirmative_answer(evidence_fixture):
    """Sin diagnósticos en la fuente, el Núcleo dice que no tiene evidencia."""
    nr = _ask("¿Cuál fue tu último diagnóstico?")
    assert nr.authority == "nucleus"
    assert nr.evidence["evidence_status"] == EvidenceStatus.EMPTY.value
    assert "no tengo evidencia" in nr.answer.lower()
    assert nr.evidence["item_count"] == 0


def test_does_not_invent_a_diagnostic(evidence_fixture):
    """La respuesta solo puede contener cifras que estén en la evidencia."""
    _seed_diagnostic(evidence_fixture["reports"], problems=3)
    nr = _ask("¿Qué problemas detectaste hoy?")
    assert nr.evidence["evidence_status"] == EvidenceStatus.OK.value
    assert "3 problema" in nr.answer
    reported = nr.evidence["items"][0]["data"]["problem_count"]
    assert reported == 3, "la cifra de la respuesta procede de la fuente"


def test_every_number_carries_its_source(evidence_fixture):
    _seed_diagnostic(evidence_fixture["reports"], problems=2)
    nr = _ask("¿Cuál fue tu último diagnóstico?")
    assert "Fuente:" in nr.answer
    for item in nr.evidence["items"]:
        assert item["source"], "todo item declara su fuente exacta"
        assert item["age_seconds"] is not None, "todo item declara su antigüedad"


def test_pending_is_never_reported_as_resolved(evidence_fixture):
    _seed_proposal(evidence_fixture["ideas"], "IDEA-FB001122", status="pending")
    nr = _ask("¿Qué propuestas tienes pendientes?")
    assert "pending" in nr.answer
    lowered = nr.answer.lower()
    for forbidden in ("resuelto", "resuelta", "solucionado", "reparado", "ejecutado"):
        assert forbidden not in lowered, f"'{forbidden}' aparece sobre una propuesta pending"


def test_approved_is_not_presented_as_a_repair(evidence_fixture):
    """Una aprobación de gobernanza NO es una reparación ejecutada."""
    _seed_proposal(evidence_fixture["ideas"], "IDEA-FB003344", status="approved")
    nr = _ask("¿Qué sabes de la propuesta IDEA-FB003344?")
    assert nr.evidence["items"][0]["status"] == "approved"
    assert nr.evidence["items"][0]["data"]["applied_at"] is None
    lowered = nr.answer.lower()
    for forbidden in ("reparad", "ejecutad", "aplicad", "corregid"):
        assert forbidden not in lowered


def test_unavailable_source_is_not_reported_as_no_problems(evidence_fixture, monkeypatch):
    """Servicio caído ≠ 'todo bien'. Son respuestas distintas."""
    def _broken(owner, owner_raw=""):
        ev = InternalEvidence(_ie.resolve_access(owner, owner_raw=owner_raw))
        monkeypatch.setattr(
            ev, "_read_reports",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fuente caída")))
        return ev
    monkeypatch.setattr(_ie, "get_internal_evidence", _broken)

    nr = _ask("¿Cuál fue tu último diagnóstico?")
    assert nr.evidence["evidence_status"] == EvidenceStatus.UNAVAILABLE.value
    assert "no está disponible" in nr.answer.lower()
    assert "no tengo evidencia" not in nr.answer.lower()


def test_unknown_proposal_id_is_not_fabricated(evidence_fixture):
    _seed_proposal(evidence_fixture["ideas"], "IDEA-FB001122")
    nr = _ask("¿Qué sabes de la propuesta IDEA-ZZZZ9999?")
    assert nr.evidence["evidence_status"] == EvidenceStatus.EMPTY.value
    assert "IDEA-ZZZZ9999" not in nr.answer or "no tengo evidencia" in nr.answer.lower()


def test_answer_is_built_without_the_llm(evidence_fixture):
    """El texto sale del constructor determinista, no de un modelo."""
    _seed_diagnostic(evidence_fixture["reports"], problems=1)
    nr = _ask("¿Cuál fue tu último diagnóstico?")
    assert nr.tool_executed == "core.nucleus.internal_evidence"
    assert nr.capability_selected == "internal_evidence"
    assert "sin LLM" in nr.reason


# ---------------------------------------------------------------------------
# 2. AUTORIZACIÓN POR IDENTIDAD CANÓNICA
# ---------------------------------------------------------------------------

def test_non_owner_cannot_read_internal_diagnostics(evidence_fixture):
    _seed_diagnostic(evidence_fixture["reports"], problems=5)
    nr = _ask("¿Cuál fue tu último diagnóstico?", owner="ana")
    assert nr.evidence["evidence_status"] == EvidenceStatus.UNAUTHORIZED.value
    assert "no estás autorizado" in nr.answer.lower()
    assert nr.evidence["item_count"] == 0
    assert "5" not in nr.answer, "no se filtra ni el conteo"


def test_non_owner_cannot_read_proposals(evidence_fixture):
    _seed_proposal(evidence_fixture["ideas"], "IDEA-FB001122")
    nr = _ask("¿Qué propuestas tienes pendientes?", owner="ana")
    assert nr.evidence["evidence_status"] == EvidenceStatus.UNAUTHORIZED.value
    assert "IDEA-FB001122" not in nr.answer


def test_authorization_does_not_depend_on_the_channel(evidence_fixture):
    """El mismo no-owner es rechazado por los tres canales por igual."""
    _seed_diagnostic(evidence_fixture["reports"], problems=4)
    statuses = {
        "api": _ask("¿Cuál fue tu último diagnóstico?", source="api", owner="ana"),
        "cli": _ask("¿Cuál fue tu último diagnóstico?", source="creator_chat", owner="ana"),
        "telegram": _ask_telegram("¿Cuál fue tu último diagnóstico?", owner="ana"),
    }
    for name, nr in statuses.items():
        assert nr.evidence["evidence_status"] == EvidenceStatus.UNAUTHORIZED.value, name
        assert "4" not in nr.answer, f"{name} filtró el conteo"


# ---------------------------------------------------------------------------
# 3. CONSISTENCIA MULTICANAL (entry points reales)
# ---------------------------------------------------------------------------

_QUESTIONS = [
    "¿Cuál fue tu último diagnóstico?",
    "¿Qué problemas detectaste hoy?",
    "¿Qué propuestas tienes pendientes?",
    "¿Qué sabes de la propuesta IDEA-FB001122?",
    "¿Qué hipótesis confirmaste?",
    "¿Qué motores tienes activos?",
    "¿Qué estás observando en tu universo?",
    "¿Cuántas estrellas y patrones tienes?",
    "¿Qué hiciste hoy en el mercado?",
    "¿Qué está ocurriendo en Freight Logistics?",
    "¿Por qué utilizaste el fallback?",
]


def _facts(nr) -> dict:
    """Los hechos que DEBEN coincidir entre canales. La redacción no entra."""
    ev = nr.evidence or {}
    return {
        "family": ev.get("family"),
        "status": ev.get("evidence_status"),
        "source": ev.get("evidence_source"),
        "item_count": ev.get("item_count"),
        "references": [i.get("reference") for i in ev.get("items", [])],
        "item_statuses": [i.get("status") for i in ev.get("items", [])],
        "authority": nr.authority,
        "final_action": nr.final_action,
    }


@pytest.mark.parametrize("question", _QUESTIONS)
def test_same_facts_across_entry_points(evidence_fixture, question):
    _seed_diagnostic(evidence_fixture["reports"], problems=3)
    _seed_proposal(evidence_fixture["ideas"], "IDEA-FB001122")

    web = _ask(question, source="api")
    cli = _ask(question, source="creator_chat")
    telegram = _ask_telegram(question)

    assert _facts(web) == _facts(cli) == _facts(telegram), (
        f"divergencia entre canales para {question!r}:\n"
        f"  web      = {_facts(web)}\n"
        f"  cli      = {_facts(cli)}\n"
        f"  telegram = {_facts(telegram)}"
    )


@pytest.mark.parametrize("question", _QUESTIONS)
def test_every_question_reaches_internal_evidence(evidence_fixture, question):
    """Criterio de comportamiento: estas preguntas deben dirigirse a la
    evidencia interna, no al LLM ni a una herramienta externa."""
    _seed_diagnostic(evidence_fixture["reports"], problems=3)
    _seed_proposal(evidence_fixture["ideas"], "IDEA-FB001122")
    nr = _ask(question)
    assert nr.evidence.get("kind") == "internal_evidence", (
        f"{question!r} no llegó a la evidencia interna (evidence={nr.evidence})"
    )
    assert nr.authority == "nucleus"


def test_generalizes_beyond_the_example_phrases(evidence_fixture):
    """Frases NO usadas en el desarrollo deben funcionar igual: la
    clasificación es topical, no una lista de frases memorizadas."""
    _seed_proposal(evidence_fixture["ideas"], "IDEA-FB001122")
    for phrase in (
        "enséñame las propuestas que siguen sin revisar",
        "listado de ideas sin aprobar",
        "quedan sugerencias pendientes?",
    ):
        nr = _ask(phrase)
        assert nr.evidence.get("family") == "proposal", f"falló: {phrase!r}"


def test_unrelated_questions_are_not_hijacked(evidence_fixture):
    """El override no puede secuestrar preguntas que no son sobre el estado
    interno — eso rompería el resto del Núcleo."""
    for phrase in ("¿qué hora es?", "hola", "¿cómo te llamas?"):
        nr = _ask(phrase)
        assert (nr.evidence or {}).get("kind") != "internal_evidence", f"secuestrada: {phrase!r}"


# Frases de conversación normal que contienen un ancla léxica interna. Las
# cinco primeras son los casos exactos del informe de auditoría 2026-09-22;
# el resto son variaciones nuevas, para que pasar exija una regla general.
_MUST_NOT_REACH_EVIDENCE = [
    "Tengo un problema con mi carro",
    "Dame ideas para mi negocio",
    "Cuál es el dominio de esta función matemática",
    "Quiero hacer una auditoría de mi empresa",
    "Tengo problemas en el mercado de Miami",
    "necesito un diagnóstico del motor del coche",
    "revisa la propuesta del proveedor antes del viernes",
    "auditoría financiera anual de la sociedad",
    "tengo un patrón raro en mis ventas de octubre",
    "quiero aprobar el presupuesto del equipo",
    "¿qué pasa después de aprobar mi crédito hipotecario?",
    "mi empresa necesita una auditoría de seguridad",
]


@pytest.mark.parametrize("phrase", _MUST_NOT_REACH_EVIDENCE)
def test_normal_conversation_never_reaches_internal_evidence(evidence_fixture, phrase):
    """Prueba NEGATIVA de integración, por el Núcleo real (no solo el
    clasificador): una conversación legítima del usuario nunca puede
    resolverse con la evidencia interna de Vectrax.

    Se siembran diagnóstico y propuesta a propósito: si el override se
    disparase, la respuesta contendría datos internos y la prueba lo vería.
    """
    _seed_diagnostic(evidence_fixture["reports"], problems=7)
    _seed_proposal(evidence_fixture["ideas"], "IDEA-FB001122")

    nr = _ask(phrase)
    assert (nr.evidence or {}).get("kind") != "internal_evidence", (
        f"{phrase!r} llegó a la evidencia interna: {nr.evidence}"
    )
    assert nr.tool_executed != "core.nucleus.internal_evidence"
    # Y no se filtra ningún dato interno en el texto.
    assert "IDEA-FB001122" not in nr.answer
    assert "7 problema" not in nr.answer


@pytest.mark.parametrize("phrase", _MUST_NOT_REACH_EVIDENCE[:5])
def test_negative_cases_are_consistent_across_channels(evidence_fixture, phrase):
    """Los tres canales deben coincidir también al NO activar."""
    _seed_diagnostic(evidence_fixture["reports"], problems=7)
    for label, nr in (("web", _ask(phrase, source="api")),
                      ("cli", _ask(phrase, source="creator_chat")),
                      ("telegram", _ask_telegram(phrase))):
        assert (nr.evidence or {}).get("kind") != "internal_evidence", f"{label}: {phrase!r}"


def test_approval_circuit_question_routes_to_the_circuit_not_the_queue(evidence_fixture):
    """«¿Qué sucede después de aprobar una propuesta?» pregunta por el
    MECANISMO, no por la cola de pendientes. Antes caía en `proposal`."""
    _seed_proposal(evidence_fixture["ideas"], "IDEA-FB001122")
    for phrase in ("¿Qué sucede después de aprobar una propuesta?",
                   "¿Qué hace el botón Aprobar?"):
        nr = _ask(phrase)
        assert nr.evidence.get("family") == "approval_pipeline", (
            f"{phrase!r} -> {nr.evidence.get('family')!r}"
        )


def test_approval_answer_never_claims_execution(evidence_fixture):
    """La traza describe dos circuitos y la ausencia de ejecutor; jamás
    afirma que algo se ejecutó o se reparó."""
    nr = _ask("¿Qué sucede después de aprobar una propuesta?")
    lowered = nr.answer.lower()
    for forbidden in ("se ejecutó", "se reparó", "se aplicó", "quedó resuelto"):
        assert forbidden not in lowered, f"la respuesta afirma ejecución: {forbidden!r}"


def test_bare_approved_word_does_not_claim_anything(evidence_fixture):
    """Decir solo «Aprobado» no puede producir una afirmación de aprobación,
    ejecución o reparación desde este sistema."""
    nr = _ask("Aprobado")
    assert (nr.evidence or {}).get("kind") != "internal_evidence"


# ---------------------------------------------------------------------------
# 4. NÚCLEO ÚNICO (estructural, sobre archivos reales — sin mocks)
# ---------------------------------------------------------------------------

_ADAPTERS = {
    "web":      "services/core/routes/chat.py",
    "cli":      "vectrax/cli.py",
    "telegram": "core/transport/pipeline_worker.py",
}


@pytest.mark.parametrize("channel,path", sorted(_ADAPTERS.items()))
def test_every_channel_delegates_to_the_canonical_nucleus(channel, path):
    src = (_ROOT / path).read_text(encoding="utf-8")
    assert "nucleus_authority" in src, f"{channel} no referencia al Núcleo canónico"
    assert ("get_nucleus_authority" in src or "NucleusAuthority" in src), (
        f"{channel} no invoca la autoridad canónica"
    )


@pytest.mark.parametrize("channel,path", sorted(_ADAPTERS.items()))
def test_no_channel_holds_parallel_cognitive_logic(channel, path):
    """Ningún adaptador puede decidir por su cuenta qué evidencia interna
    mostrar: si lo hiciera, dos canales podrían divergir de nuevo."""
    src = (_ROOT / path).read_text(encoding="utf-8")
    for forbidden in ("internal_evidence", "evidence_intent"):
        assert forbidden not in src, (
            f"{channel} ({path}) consulta la evidencia interna por su cuenta; "
            "debe hacerlo solo a través del Núcleo"
        )


def test_the_nucleus_is_the_only_caller_of_the_evidence_facade():
    """Invariante de autoridad única sobre el árbol real de producción."""
    from core.nucleus.internal_evidence import _SCAN_EXCLUDED_DIRS
    callers = []
    for py in _ROOT.rglob("*.py"):
        # Mismo recorte que `approval_pipeline()`: sin él, en la máquina de
        # desarrollo esto entra en `.venv/` y tarda decenas de segundos.
        if set(py.parts) & _SCAN_EXCLUDED_DIRS:
            continue
        if py.parent.name == "nucleus" and py.parent.parent.name == "core":
            continue
        try:
            if "core.nucleus.internal_evidence" in py.read_text(encoding="utf-8", errors="ignore"):
                callers.append(str(py.relative_to(_ROOT)))
        except OSError:
            continue
    assert callers == [], (
        f"la evidencia interna se consulta fuera del Núcleo: {callers}"
    )
