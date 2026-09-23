"""
tests/test_audit_ledger_isolation.py — El ledger no escribe donde no debe.

EL DEFECTO
----------
`core/audit_ledger.py` calculaba su ruta al IMPORTAR::

    VAULT_DIR = os.environ.get("VECTRAX_VAULT_DIR", "~/Vectrax/vault")
    LEDGER_PATH = os.path.join(VAULT_DIR, "audit_ledger.db")

Se congelaba en el primer import y `VECTRAX_VAULT_DIR` dejaba de tener efecto
después. En la suite eso significaba que TODAS las pruebas escribían en el
vault que estuviera activo cuando el módulo se importó por primera vez. Es el
mismo defecto de ruta congelada que PR #124 corrigió en `learned_rules`.

Importa más ahora: las rutas constitucionales de este PR asientan aquí sus
bloqueos y sus indisponibilidades. Una auditoría que se escribe en un sitio
impredecible no es una auditoría.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import audit_ledger  # noqa: E402

#: El ledger REAL del repositorio. Estas pruebas no pueden tocarlo.
_REAL_LEDGER = _ROOT / "vault" / "audit_ledger.db"


def _state(path: Path):
    """Hash + tamaño + mtime. `None` si no existe."""
    if not path.is_file():
        return None
    data = path.read_bytes()
    st = path.stat()
    return (hashlib.sha256(data).hexdigest(), st.st_size, st.st_mtime_ns)


@pytest.fixture(autouse=True)
def _guard_the_real_ledger():
    """GUARD: el `vault/audit_ledger.db` real no puede cambiar.

    Se comprueba antes y después de CADA prueba de este archivo.
    """
    before = _state(_REAL_LEDGER)
    yield
    after = _state(_REAL_LEDGER)
    assert after == before, (
        f"las pruebas modificaron el ledger REAL en {_REAL_LEDGER}"
    )


# ===========================================================================
# 1. La ruta se resuelve al ACCEDER, no al importar
# ===========================================================================

class TestThePathIsResolvedAtAccessTime:

    def test_the_env_var_is_honoured_after_import(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "v1"))
        assert audit_ledger.vault_dir() == str(tmp_path / "v1")
        # Y cambiarla DESPUÉS también se respeta: no hay singleton congelado.
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "v2"))
        assert audit_ledger.vault_dir() == str(tmp_path / "v2")
        assert audit_ledger.ledger_path() == str(
            tmp_path / "v2" / "audit_ledger.db"
        )

    def test_no_override_keeps_exactly_the_production_path(self, monkeypatch):
        """La ruta de producción de hoy no cambia."""
        monkeypatch.delenv("VECTRAX_VAULT_DIR", raising=False)
        expected = os.path.join(
            os.path.expanduser("~"), "Vectrax", "vault", "audit_ledger.db",
        )
        assert audit_ledger.ledger_path() == expected
        assert audit_ledger.PRODUCTION_VAULT_DIR == os.path.join(
            os.path.expanduser("~"), "Vectrax", "vault",
        )

    def test_an_empty_override_falls_back_to_production(self, monkeypatch):
        monkeypatch.setenv("VECTRAX_VAULT_DIR", "")
        assert audit_ledger.vault_dir() == audit_ledger.PRODUCTION_VAULT_DIR

    def test_an_explicit_path_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "ignorado"))
        explicit = str(tmp_path / "explicito.db")
        assert audit_ledger.ledger_path(explicit) == explicit

    def test_the_frozen_attributes_are_gone(self):
        """Si volvieran, volvería el defecto."""
        for attr in ("VAULT_DIR", "LEDGER_PATH"):
            assert not hasattr(audit_ledger, attr), (
                f"`{attr}` volvió a ser un atributo congelado a nivel de módulo"
            )


# ===========================================================================
# 2. Importar o LEER no crea nada
# ===========================================================================

class TestReadingCreatesNothing:

    def test_resolving_the_path_creates_nothing(self, tmp_path, monkeypatch):
        target = tmp_path / "jamas_creado"
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(target))
        audit_ledger.ledger_path()
        audit_ledger.vault_dir()
        assert not target.exists()

    def test_query_on_a_missing_ledger_returns_empty_without_creating(
        self, tmp_path, monkeypatch,
    ):
        target = tmp_path / "sin_ledger"
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(target))
        assert audit_ledger.query(limit=10) == []
        assert audit_ledger.count() == 0
        assert not target.exists(), "una lectura creó el almacén"

    def test_writing_does_create_it(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "v"))
        audit_ledger.record("prueba", reason="r")
        assert (tmp_path / "v" / "audit_ledger.db").is_file()


# ===========================================================================
# 3. Dos vaults temporales quedan aislados
# ===========================================================================

class TestTwoVaultsAreIsolated:

    def test_entries_do_not_leak_between_vaults(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "a"))
        audit_ledger.record("solo_en_a", reason="a")

        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "b"))
        audit_ledger.record("solo_en_b", reason="b")

        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "a"))
        acciones_a = {r["action"] for r in audit_ledger.query(limit=50)}
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "b"))
        acciones_b = {r["action"] for r in audit_ledger.query(limit=50)}

        assert "solo_en_a" in acciones_a and "solo_en_b" not in acciones_a
        assert "solo_en_b" in acciones_b and "solo_en_a" not in acciones_b

    def test_an_explicit_path_is_isolated_too(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "env"))
        otro = str(tmp_path / "otro.db")
        audit_ledger.record("en_otro", reason="x", db_path=otro)
        assert audit_ledger.count(db_path=otro) == 1
        assert audit_ledger.count() == 0, "escribió también en el vault del env"


# ===========================================================================
# 4. El subproceso escribe y lee SOLO en el vault temporal
# ===========================================================================

class TestTheSubprocessStaysInTheTempVault:

    def test_a_child_process_uses_the_temp_vault(self, tmp_path, monkeypatch):
        vault = tmp_path / "hijo"
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(vault))

        env = dict(os.environ, VECTRAX_VAULT_DIR=str(vault))
        code = (
            "import sys; sys.path.insert(0, sys.argv[1])\n"
            "from core import audit_ledger\n"
            "audit_ledger.record('desde_el_hijo', reason='r')\n"
            "print(audit_ledger.ledger_path())\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", code, str(_ROOT)],
            capture_output=True, text=True, timeout=120, env=env,
        )
        assert out.returncode == 0, out.stderr
        child_path = out.stdout.strip().splitlines()[-1]
        assert child_path == str(vault / "audit_ledger.db"), (
            f"el hijo escribió en {child_path}, fuera del vault temporal"
        )
        # Y el padre lo ve, porque es el MISMO vault temporal.
        assert "desde_el_hijo" in {
            r["action"] for r in audit_ledger.query(limit=50)
        }
