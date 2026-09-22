"""
tests/test_route_authorization_integration.py — La autorización REAL de dos rutas.

POR QUÉ HACE FALTA
------------------
`tests/test_permission_literals_contract.py` demuestra por AST que cada literal
de permiso existe en `core.roles.Permission`, y las pruebas de roles comprueban
`has_permission()` en aislamiento. Ninguna de las dos ATRAVIESA la dependencia
real: `require_permission()` -> `Depends(require_token)` -> `has_permission()`
-> `HTTPException(403)`. El defecto original (`"core.write"`, inexistente,
403 para todos incluido `owner`) vivía justo en ese recorrido.

Esta prueba lo recorre entero sobre dos rutas representativas, una por cada
permiso corregido:

  POST /v1/ideas/{id}/approve   -> apply_proposal
  POST /v1/gravitational/recompute -> core.admin

AISLAMIENTO
-----------
* La identidad se inyecta sustituyendo la dependencia `require_token`, así que
  `require_permission()` y `has_permission()` se ejecutan DE VERDAD — que es lo
  que se quiere verificar. Lo único simulado es de quién es la petición.
* Las operaciones mutantes (`IdeaStore.approve()`, `recompute_all_masses()`)
  se sustituyen por dobles que solo registran la llamada.
* `_get_token_manager` se sustituye por un doble que EXPLOTA: si alguna ruta
  llegara a validar un token de verdad, abriría `~/.vectrax/vectrax.db`. La
  prueba de "sin autenticación" usa la ausencia de cabecera, que corta antes.

No se escribe en ninguna base, vault ni archivo real.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from services.core.auth import AuthContext, require_token  # noqa: E402

# (ruta, método, permiso que exige, nombre del doble que debe invocarse)
IDEAS_APPROVE = "/v1/ideas/IDEA-ABC123/approve"
GRAV_RECOMPUTE = "/v1/gravitational/recompute"


class _Recorder:
    """Doble mínimo: cuenta invocaciones y no toca nada."""

    def __init__(self, result):
        self.calls = []
        self._result = result

    def __call__(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return self._result

    @property
    def called(self) -> bool:
        return bool(self.calls)


class _IdeaDouble:
    def to_dict(self):
        return {"idea_id": "IDEA-ABC123", "status": "approved"}


class _IdeaStoreDouble:
    """Sustituye a `IdeaStore`. `approve()` no escribe: registra."""

    def __init__(self):
        self.approve_calls = []

    def approve(self, idea_id, by="", note=""):
        self.approve_calls.append({"idea_id": idea_id, "by": by, "note": note})
        return True

    def get_by_id(self, idea_id):
        return _IdeaDouble()


def _ctx(role: str) -> AuthContext:
    return AuthContext(
        user_id=f"u_{role}",
        username=role,
        role=role,
        channel="creator" if role == "owner" else "user",
        owner=role,
    )


@pytest.fixture
def wired(monkeypatch):
    """App con las dos rutas reales y las operaciones mutantes sustituidas."""
    from services.core.routes.gravitational import router as grav_router
    from services.core.routes.ideas import router as ideas_router

    app = FastAPI()
    app.include_router(ideas_router, prefix="/v1")
    app.include_router(grav_router, prefix="/v1")

    # -- dobles de las operaciones mutantes -----------------------------
    store = _IdeaStoreDouble()
    import core.idea_store as _idea_store
    monkeypatch.setattr(_idea_store, "get_idea_store", lambda: store)

    recompute = _Recorder({"updated": 0})
    import vectrax.cognitive_gravity as _cg
    monkeypatch.setattr(_cg, "recompute_all_masses", recompute, raising=False)

    # -- ninguna ruta puede llegar a abrir la base de tokens ------------
    def _explode():
        raise AssertionError(
            "se intentó validar un token real: eso abriría ~/.vectrax/vectrax.db"
        )
    import services.core.auth as _auth
    monkeypatch.setattr(_auth, "_get_token_manager", _explode)

    def as_role(role: str) -> TestClient:
        app.dependency_overrides[require_token] = lambda: _ctx(role)
        return TestClient(app)

    def anonymous() -> TestClient:
        app.dependency_overrides.pop(require_token, None)
        return TestClient(app)

    yield {
        "as_role": as_role,
        "anonymous": anonymous,
        "store": store,
        "recompute": recompute,
    }
    app.dependency_overrides.clear()


# ===========================================================================
# POST /v1/ideas/{id}/approve  ->  apply_proposal
# ===========================================================================

class TestIdeasApproveRequiresApplyProposal:

    def test_owner_passes_authorization(self, wired):
        r = wired["as_role"]("owner").post(IDEAS_APPROVE, json={"note": "ok"})
        assert r.status_code == 200, r.text
        assert r.json()["approved"] is True

    def test_owner_is_the_only_one_that_reaches_the_operation(self, wired):
        wired["as_role"]("owner").post(IDEAS_APPROVE, json={"note": "ok"})
        assert len(wired["store"].approve_calls) == 1
        assert wired["store"].approve_calls[0]["idea_id"] == "IDEA-ABC123"
        assert wired["store"].approve_calls[0]["by"] == "u_owner"

    def test_operator_is_denied(self, wired):
        r = wired["as_role"]("operator").post(IDEAS_APPROVE, json={"note": "x"})
        assert r.status_code == 403
        assert "apply_proposal" in r.json()["detail"]
        assert wired["store"].approve_calls == [], "no debe ejecutarse la operación"

    def test_viewer_is_denied(self, wired):
        r = wired["as_role"]("viewer").post(IDEAS_APPROVE, json={"note": "x"})
        assert r.status_code == 403
        assert "apply_proposal" in r.json()["detail"]
        assert wired["store"].approve_calls == []

    def test_missing_authentication(self, wired):
        r = wired["anonymous"]().post(IDEAS_APPROVE, json={"note": "x"})
        assert r.status_code == 401
        assert "Authorization" in r.json()["detail"]
        assert wired["store"].approve_calls == []


# ===========================================================================
# POST /v1/gravitational/recompute  ->  core.admin
# ===========================================================================

class TestGravitationalRecomputeRequiresCoreAdmin:

    def test_owner_passes_authorization(self, wired):
        r = wired["as_role"]("owner").post(GRAV_RECOMPUTE)
        assert r.status_code == 200, r.text

    def test_owner_is_the_only_one_that_reaches_the_operation(self, wired):
        wired["as_role"]("owner").post(GRAV_RECOMPUTE)
        assert len(wired["recompute"].calls) == 1
        assert wired["recompute"].calls[0]["kwargs"]["owner"] == "owner"

    def test_operator_is_denied(self, wired):
        r = wired["as_role"]("operator").post(GRAV_RECOMPUTE)
        assert r.status_code == 403
        assert "core.admin" in r.json()["detail"]
        assert not wired["recompute"].called, "no debe ejecutarse la operación"

    def test_viewer_is_denied(self, wired):
        r = wired["as_role"]("viewer").post(GRAV_RECOMPUTE)
        assert r.status_code == 403
        assert "core.admin" in r.json()["detail"]
        assert not wired["recompute"].called

    def test_missing_authentication(self, wired):
        r = wired["anonymous"]().post(GRAV_RECOMPUTE)
        assert r.status_code == 401
        assert "Authorization" in r.json()["detail"]
        assert not wired["recompute"].called


# ===========================================================================
# El recorrido que se está ejercitando es el real
# ===========================================================================

class TestTheDependencyIsNotStubbed:

    def test_a_nonexistent_permission_would_deny_even_the_owner(self, wired, monkeypatch):
        """Reproduce el defecto original sobre la ruta real.

        Si `require_permission` estuviera sustituido por un doble, esto pasaría
        igual que el caso feliz. Al fallar con 403 para `owner`, queda
        demostrado que estas pruebas atraviesan `has_permission()` de verdad.
        """
        from fastapi import Depends, FastAPI as _FastAPI
        from services.core.middleware.user_context import require_permission

        probe = _FastAPI()

        @probe.post("/probe")
        async def _probe(ctx: AuthContext = Depends(require_permission("core.write"))):
            return {"reached": True}

        probe.dependency_overrides[require_token] = lambda: _ctx("owner")
        r = TestClient(probe).post("/probe")
        assert r.status_code == 403
        assert "core.write" in r.json()["detail"]

    def test_the_same_probe_passes_with_a_real_permission(self, wired):
        from fastapi import Depends, FastAPI as _FastAPI
        from services.core.middleware.user_context import require_permission

        probe = _FastAPI()

        @probe.post("/probe")
        async def _probe(ctx: AuthContext = Depends(require_permission("apply_proposal"))):
            return {"reached": True}

        probe.dependency_overrides[require_token] = lambda: _ctx("owner")
        r = TestClient(probe).post("/probe")
        assert r.status_code == 200
        assert r.json()["reached"] is True
