"""
tests/test_approval_pipeline_permissions.py — La traza no puede quedarse vieja.

DEFECTO QUE PREVIENE
--------------------
`services/core/routes/ideas.py` pasó de exigir `"core.write"` (un permiso que
no existe en `core.roles.Permission`, y que por tanto devolvía 403 a todos los
roles) a exigir `apply_proposal`. Pero
`core/nucleus/internal_evidence.py::approval_pipeline()` siguió AFIRMANDO
`core.write` en dos sitios: su docstring y el `EvidenceItem` de scope
`ideas/1.endpoint`.

Eso es peor que un comentario obsoleto: `approval_pipeline()` es lo que el
Núcleo le responde al owner cuando pregunta qué pasa al aprobar una propuesta.
La respuesta visible citaba un permiso inexistente como si fuera el actual.

QUÉ VERIFICA
------------
1. La traza nombra `apply_proposal` en el ítem `ideas/1.endpoint`.
2. Ese ítem no menciona `core.write`.
3. El TEXTO que ve el usuario (`evidence_intent.build_answer()`) tampoco.
4. El literal de la traza coincide con el permiso REAL de la ruta, leído por
   AST — el guard que habría detectado la desincronización original.

La traza solo lee archivos del repositorio; no escribe en ninguna base, vault
ni archivo.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys
from typing import Optional

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.nucleus.evidence_intent import build_answer  # noqa: E402
from core.nucleus.internal_evidence import (  # noqa: E402
    EvidenceAccess,
    EvidenceStatus,
    InternalEvidence,
)

OWNER = EvidenceAccess(owner_canonical="mario", is_owner=True, owner_raw="mario")
STRANGER = EvidenceAccess(owner_canonical="ana", is_owner=False, owner_raw="tg:999")

_IDEAS_ROUTES = _ROOT / "services" / "core" / "routes" / "ideas.py"


@pytest.fixture(scope="module")
def pipeline():
    """La traza, calculada una vez: recorre el repo y no hace falta repetirlo."""
    return InternalEvidence(OWNER).approval_pipeline()


def _item(result, scope: str):
    for item in result.items:
        if item.scope == scope:
            return item
    raise AssertionError(
        f"no existe el ítem de scope {scope!r}; scopes presentes: "
        f"{[i.scope for i in result.items]}"
    )


def _route_permission(function_name: str) -> Optional[str]:
    """Permiso REAL que exige un endpoint, leído del AST de la ruta.

    Busca en la función `function_name` la llamada
    `require_permission("<literal>")` y devuelve ese literal.
    """
    tree = ast.parse(_IDEAS_ROUTES.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != function_name:
            continue
        for call in ast.walk(node.args):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != "require_permission":
                continue
            for arg in call.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    return arg.value
    return None


class TestRoutePermissionFixture:
    """Guard del propio ayudante: si deja de leer la ruta, el resto pasaría vacío."""

    def test_ast_helper_finds_the_real_permission(self):
        assert _route_permission("approve_idea") == "apply_proposal"
        assert _route_permission("reject_idea") == "apply_proposal"
        assert _route_permission("refresh_ideas") == "core.admin"
        assert _route_permission("no_existe_esta_funcion") is None


class TestApprovalPipelineTrace:

    def test_pipeline_is_readable_by_owner(self, pipeline):
        assert pipeline.status in (EvidenceStatus.OK, EvidenceStatus.STALE)
        assert pipeline.items, "la traza no puede venir vacía"

    def test_ideas_endpoint_item_names_apply_proposal(self, pipeline):
        item = _item(pipeline, "ideas/1.endpoint")
        assert "apply_proposal" in item.summary

    def test_ideas_endpoint_item_does_not_name_core_write(self, pipeline):
        item = _item(pipeline, "ideas/1.endpoint")
        assert "core.write" not in item.summary, (
            "La traza sigue afirmando un permiso que la ruta ya no usa "
            "(y que además no existe en core.roles.Permission)."
        )

    def test_trace_matches_the_real_route_permission(self, pipeline):
        """La afirmación de la traza y el literal de la ruta no pueden divergir.

        Este es el guard que faltaba: es exactamente la desincronización que
        dejó `core.write` en la respuesta conversacional después de corregir
        la ruta.
        """
        real = _route_permission("approve_idea")
        assert real, "no se pudo leer el permiso real de approve_idea"
        item = _item(pipeline, "ideas/1.endpoint")
        cited = re.search(r"permiso\s+([A-Za-z0-9_.]+)", item.summary)
        assert cited, f"la traza no cita ningún permiso: {item.summary!r}"
        assert cited.group(1) == real, (
            f"la traza dice {cited.group(1)!r} y la ruta exige {real!r}"
        )

    def test_proposals_endpoint_item_is_unchanged(self, pipeline):
        """El otro circuito ya citaba el permiso correcto; no se toca."""
        item = _item(pipeline, "proposals/1.endpoint")
        assert "apply_proposal" in item.summary
        assert "core.write" not in item.summary

    def test_no_item_mentions_core_write(self, pipeline):
        offenders = [
            f"{i.scope}: {i.summary}" for i in pipeline.items if "core.write" in i.summary
        ]
        assert not offenders, "ítems que aún citan core.write:\n  " + "\n  ".join(offenders)

    def test_the_two_circuits_are_still_reported_as_independent(self, pipeline):
        """No se ha alterado la conclusión principal de la traza."""
        item = _item(pipeline, "relacion")
        assert item.status == "independent"


class TestVisibleAnswer:
    """Lo que de verdad lee el owner en el chat."""

    def test_answer_names_apply_proposal(self, pipeline):
        answer = build_answer(pipeline)
        assert "apply_proposal" in answer

    def test_answer_never_names_core_write(self, pipeline):
        answer = build_answer(pipeline)
        assert "core.write" not in answer, (
            "La respuesta visible cita un permiso inexistente como si fuera "
            f"el actual:\n{answer}"
        )

    def test_answer_shows_the_whole_trace(self, pipeline):
        """`approval_pipeline` tiene límite de display 32: no se recorta.

        Si se recortara, la afirmación del endpoint podría desaparecer detrás
        de un "(+N más)" y esta prueba dejaría de proteger nada.
        """
        answer = build_answer(pipeline)
        assert "más)" not in answer
        assert "ideas/1.endpoint" in answer

    def test_stranger_gets_no_trace_at_all(self):
        result = InternalEvidence(STRANGER).approval_pipeline()
        assert result.status is EvidenceStatus.UNAUTHORIZED
        assert result.items == []
        answer = build_answer(result)
        assert "apply_proposal" not in answer
        assert "core.write" not in answer
