"""
tests/test_permission_literals_contract.py — Contrato de literales de permiso.

DEFECTO QUE PREVIENE
--------------------
`services/core/routes/{ideas,gravitational}.py` exigían el permiso
`"core.write"`, que NUNCA existió en `core.roles.Permission`.
`has_permission()` captura el `ValueError` de `Permission("core.write")` y
devuelve `False` (core/roles.py), así que `require_permission()` convertía esas
rutas en un 403 permanente para TODOS los roles — incluido `owner`. El fallo era
silencioso: ninguna prueba comparaba los literales escritos en las rutas contra
el enum real.

QUÉ VERIFICA
------------
Recorre ESTÁTICAMENTE (AST, sin importar las rutas ni levantar la app) todo el
repositorio buscando llamadas a `require_permission(...)` y
`has_permission(..., ...)` cuyo argumento de permiso sea un literal de cadena, y
exige que ese literal exista en `core.roles.Permission`.

El conjunto de valores válidos se DERIVA del enum real
(`{p.value for p in Permission}`) — nunca de una lista copiada a mano, que se
desincronizaría en cuanto alguien añadiera un permiso.

Cualquier literal nuevo e inexistente hace fallar esta prueba nombrando el
archivo y la línea exactos.
"""
from __future__ import annotations

import ast
import pathlib
from typing import Dict, List, Tuple

import pytest

from core.roles import Permission, Role, has_permission

# Raíz del repositorio, derivada de la ubicación de este archivo.
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Directorios que no forman parte del código vivo de la aplicación.
#
# `tests` se excluye a propósito: una prueba puede (y debe) pasar un permiso
# inexistente para comprobar que el RBAC falla de forma cerrada — ver
# `TestUnknownPermissionFailsClosed` más abajo, que usa "core.write"
# deliberadamente. Sin esta exclusión el escáner se encontraría a sí mismo. El
# contrato aplica al código que ATIENDE peticiones: rutas y middleware.
_SKIP_DIRS = frozenset({
    ".git", "archive", ".venv", "venv", "env", "__pycache__",
    "node_modules", "build", "dist", ".pytest_cache", "site-packages",
    "tests",
})

# Nombre de la función -> índice posicional del argumento que lleva el permiso.
#   require_permission(perm)            -> 0
#   has_permission(role, perm)          -> 1
_PERMISSION_ARG_INDEX: Dict[str, int] = {
    "require_permission": 0,
    "has_permission": 1,
}

# Nombres de argumento por palabra clave que también llevan el permiso.
_PERMISSION_KWARGS = frozenset({"perm", "permission"})


def _iter_python_files():
    for path in _REPO_ROOT.rglob("*.py"):
        # `core/alerta.py` es un DIRECTORIO en este repositorio: sin este guard,
        # `read_text()` lanza IsADirectoryError y el escaneo se cae.
        if not path.is_file():
            continue
        if set(path.parts) & _SKIP_DIRS:
            continue
        yield path


def _collect_permission_literals() -> List[Tuple[str, str, int]]:
    """Devuelve [(literal, ruta_relativa, línea), ...] de todo el repositorio."""
    found: List[Tuple[str, str, int]] = []
    for path in _iter_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except (SyntaxError, ValueError):
            # Un archivo no parseable no es asunto de esta prueba.
            continue
        rel = str(path.relative_to(_REPO_ROOT))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            index = _PERMISSION_ARG_INDEX.get(name)
            if index is None:
                continue

            candidates = []
            if len(node.args) > index:
                candidates.append(node.args[index])
            for kw in node.keywords:
                if kw.arg in _PERMISSION_KWARGS:
                    candidates.append(kw.value)

            for arg in candidates:
                # Solo los literales de cadena son verificables estáticamente.
                # `Permission.CORE_READ` o una variable ya están tipados o se
                # resuelven en runtime; no son el defecto que esto previene.
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.append((arg.value, rel, node.lineno))
    return found


class TestPermissionLiteralsExist:
    """Todo literal de permiso escrito en el código debe existir en el enum."""

    def test_scanner_finds_the_real_call_sites(self):
        """Guard del propio escáner: si deja de encontrar llamadas, la prueba
        pasaría vacía y no protegería nada."""
        literals = _collect_permission_literals()
        assert len(literals) >= 10, (
            f"El escáner solo encontró {len(literals)} literales de permiso. "
            "Es sospechosamente pocos: revisa _PERMISSION_ARG_INDEX y _SKIP_DIRS."
        )
        files = {rel for _, rel, _ in literals}
        assert any("services/core/routes" in f for f in files), (
            "El escáner no está viendo services/core/routes/*.py"
        )

    def test_every_literal_is_a_real_permission(self):
        valid = {p.value for p in Permission}  # derivado del enum, nunca a mano
        invalid = [
            f"{rel}:{line} -> {literal!r}"
            for literal, rel, line in _collect_permission_literals()
            if literal not in valid
        ]
        assert not invalid, (
            "Permisos inexistentes en core.roles.Permission (has_permission() "
            "devuelve False para ellos, así que la ruta queda en 403 para TODOS "
            "los roles, owner incluido):\n  "
            + "\n  ".join(sorted(invalid))
            + f"\n\nPermisos válidos: {sorted(valid)}"
        )

    def test_core_write_is_gone(self):
        """Regresión explícita del defecto original."""
        literals = {literal for literal, _, _ in _collect_permission_literals()}
        assert "core.write" not in literals, (
            "Ha reaparecido 'core.write'. No es un miembro de Permission; usa "
            "'core.admin' (administración) o 'apply_proposal' (aprobar/rechazar)."
        )


class TestUnknownPermissionFailsClosed:
    """El motor RBAC debe seguir negando por defecto ante lo desconocido."""

    @pytest.mark.parametrize("role", [r.value for r in Role])
    def test_unknown_permission_denied_for_every_role(self, role):
        assert has_permission(role, "core.write") is False
        assert has_permission(role, "no.existe") is False

    def test_unknown_role_denied(self):
        assert has_permission("superadmin", "core.read") is False


class TestChangedEndpointPermissions:
    """Matriz de roles de los permisos que ahora exigen las rutas corregidas."""

    @pytest.mark.parametrize("perm", ["apply_proposal", "core.admin"])
    def test_owner_allowed(self, perm):
        assert has_permission("owner", perm) is True

    @pytest.mark.parametrize("perm", ["apply_proposal", "core.admin"])
    @pytest.mark.parametrize("role", ["operator", "viewer"])
    def test_operator_and_viewer_still_denied(self, role, perm):
        assert has_permission(role, perm) is False

    @pytest.mark.parametrize("role", ["owner", "operator", "viewer"])
    def test_read_permissions_unchanged(self, role):
        """La corrección no toca lectura: los tres roles conservan core.read."""
        assert has_permission(role, "core.read") is True


class TestRoutesUseTheCorrectedPermissions:
    """Las rutas concretas que se corrigieron, verificadas por AST."""

    @staticmethod
    def _literals_in(relative_path: str) -> List[str]:
        return [
            literal
            for literal, rel, _ in _collect_permission_literals()
            if rel == relative_path
        ]

    def test_ideas_routes(self):
        lits = self._literals_in("services/core/routes/ideas.py")
        assert lits.count("apply_proposal") == 2, "approve y reject"
        assert lits.count("core.admin") == 1, "refresh"
        assert lits.count("core.read") == 1, "listado, sin cambios"
        assert "core.write" not in lits

    def test_gravitational_routes(self):
        lits = self._literals_in("services/core/routes/gravitational.py")
        assert lits.count("core.admin") == 6, "las seis escrituras"
        assert "core.write" not in lits
