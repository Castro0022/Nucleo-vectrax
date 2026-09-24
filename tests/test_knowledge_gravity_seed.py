"""
Tests — connectors/etoro/knowledge_gravity_seed (corrección 2026-09-24).

Cubre la ruta CORREGIDA: las estrellas de conocimiento TA-Lib se crean vía
`vectrax.engine.ingest()` — la puerta ya existente del universo cognitivo —
NO vía `GravityIndex`/`GravityRecord`. Verifica, con la mecánica real (no
mockeada) de `compute_star_gravity`/`assign_layer`, que una estrella nueva
nace en OUTER por cálculo, no por asignación manual; que no se crea ningún
GravityRecord; y que la identidad/deduplicación es la que ya tiene
`ingest()`, sin capa propia.

Mismo patrón de aislamiento que tests/core/test_core_pipeline.py: DB
temporal + embed() determinista (sin red, sin sentence-transformers real).

Run: python -m pytest tests/test_knowledge_gravity_seed.py -v
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import vectrax.engine  # noqa: F401 - asegura el submódulo importado para patch()


def _fake_embed(text: str):
    """Embedding determinista de 8 dims — mismo patrón que
    tests/core/test_core_pipeline.py::_fake_embed, sin red ni
    sentence-transformers real."""
    h = hashlib.md5(text.encode()).digest()
    return np.array([float(b) / 255.0 for b in h[:8]], dtype=np.float32)


@pytest.fixture
def isolated_db(tmp_path):
    """DB de vectrax.db temporal, aislada de cualquier dato real."""
    import vectrax.db as db_mod
    db_mod.DB_DIR = tmp_path
    db_mod.DB_PATH = tmp_path / "vectrax.db"
    db_mod.init_db()
    yield db_mod


@pytest.fixture
def seed_module(isolated_db):
    """Importa el módulo bajo prueba DESPUÉS de aislar la DB (mismo orden
    que test_core_pipeline.py: redirigir antes de que el engine la toque)."""
    import connectors.etoro.knowledge_gravity_seed as seed
    return seed


def _referenced_names(*functions) -> set:
    """Nombres realmente referenciados por el CÓDIGO de estas funciones
    (variables/atributos/imports) -- vía co_names, que excluye docstrings y
    literales de texto. Más preciso que buscar substrings en el código
    fuente completo (que incluiría la prosa explicativa del módulo)."""
    names = set()
    for fn in functions:
        names.update(fn.__code__.co_names)
        for const in fn.__code__.co_consts:
            if hasattr(const, "co_names"):
                names.update(const.co_names)
    return names


class TestSeedUsesIngestNotGravityRecord:
    def test_does_not_import_or_touch_gravity_engine(self, seed_module):
        """El CÓDIGO (no la prosa explicativa del docstring) no debe
        referenciar NINGÚN nombre de GravityIndex/GravityRecord/
        gravity_engine ni sus métodos de escritura."""
        names = _referenced_names(
            seed_module.seed_knowledge_stars, seed_module._knowledge_text,
        )
        forbidden = {
            "GravityIndex", "GravityRecord", "gravity_engine",
            "get_gravity_index", "record_event", "update_records",
        }
        assert not (names & forbidden), f"referencias prohibidas encontradas: {names & forbidden}"

    def test_seeding_creates_zero_gravity_records(self, seed_module, tmp_path, monkeypatch):
        """Verificación en vivo: tras sembrar, el índice de gravedad (en un
        path aislado) sigue vacío — ni un solo GravityRecord nuevo."""
        from core.learn.gravity_engine import GravityIndex

        gravity_path = tmp_path / "gravity_index.json"
        idx = GravityIndex(path=str(gravity_path))
        with patch("core.learn.gravity_engine.get_gravity_index", return_value=idx), \
             patch("vectrax.engine.embed", side_effect=_fake_embed), \
             patch("vectrax.engine.find_similar", return_value=[]):
            seed_module.seed_knowledge_stars()

        # Ni el archivo se creó (nadie llamó a _write_to_disk).
        assert not gravity_path.exists()
        assert idx.load_raw() == {}


class TestSeedingBehavior:
    def test_seeds_all_known_functions_into_vectrax_db(self, seed_module):
        from vectrax.db import get_all_stars
        from connectors.market.ta_knowledge import known_function_names

        with patch("vectrax.engine.embed", side_effect=_fake_embed), \
             patch("vectrax.engine.find_similar", return_value=[]):
            summary = seed_module.seed_knowledge_stars()

        names = known_function_names()
        assert summary["total_candidates"] == len(names)
        assert len(summary["created"]) == len(names)
        assert summary["errors"] == 0

        stars = get_all_stars(channel="user", owner="vectrax_system")
        primary = [s for s in stars if s.star_type == "primary"]
        assert len(primary) == len(names)
        # Puede haber MÁS filas que 196: _post_ingest() detecta convergencia
        # y constelación como efecto emergente real (star_type='convergence'),
        # no algo que este módulo fuerce ni cuente como "creada". Nunca menos.
        assert len(stars) >= len(names)

    def test_stars_are_channel_user_owner_vectrax_system(self, seed_module):
        with patch("vectrax.engine.embed", side_effect=_fake_embed), \
             patch("vectrax.engine.find_similar", return_value=[]):
            summary = seed_module.seed_knowledge_stars()

        for row in summary["stars"]:
            assert row["channel"] == "user"
            assert row["owner"] == "vectrax_system"

    def test_layer_is_outer_by_natural_computation_not_assignment(self, seed_module):
        """No se asigna 'outer' en ningún lado del módulo (ya verificado en
        TestSeedUsesIngestNotGravityRecord vía fuente) — aquí se comprueba
        el RESULTADO real: compute_star_gravity()+assign_layer(), corriendo
        de verdad (sin mockear), colocan la estrella recién nacida en outer."""
        with patch("vectrax.engine.embed", side_effect=_fake_embed), \
             patch("vectrax.engine.find_similar", return_value=[]):
            summary = seed_module.seed_knowledge_stars()

        from vectrax.models import GRAVITY_MID_THRESHOLD
        for row in summary["stars"]:
            assert row["layer"] == "outer"
            assert row["gravity_score"] < GRAVITY_MID_THRESHOLD

    def test_mass_is_min_mass_not_artificial(self, seed_module):
        from vectrax.models import MIN_MASS

        with patch("vectrax.engine.embed", side_effect=_fake_embed), \
             patch("vectrax.engine.find_similar", return_value=[]):
            summary = seed_module.seed_knowledge_stars()

        for row in summary["stars"]:
            assert abs(row["mass"] - MIN_MASS) < 1e-9

    def test_module_source_never_hardcodes_layer_or_mass(self, seed_module):
        """Cinturón y tirantes sobre la fuente: ni 'outer'/'OUTER' ni
        asignación de mass aparecen en el módulo — el resultado de arriba
        es 100% producto de ingest(), no de una constante local."""
        import inspect
        source = inspect.getsource(seed_module)
        assert '"outer"' not in source.lower()
        assert "star.layer =" not in source
        assert "star.mass =" not in source
        assert "star.gravity_score =" not in source


class TestIdentityIsIngestOwn:
    def test_module_does_not_add_its_own_identity_layer(self, seed_module):
        """El módulo no debe definir NINGÚN concepto de fingerprint/id
        propio -- toda identidad viene de ingest()/embeddings."""
        import inspect
        source = inspect.getsource(seed_module)
        assert "fingerprint" not in source.lower()

    def test_rerunning_finds_near_duplicates_not_197_new_stars(self, seed_module):
        """ingest() decide identidad por similitud de embedding. Con el
        MISMO texto (mismo hash -> mismo embedding determinista), la
        segunda siembra debe reconocerlas como YA VISTAS, no crear el
        doble. Esto reporta el resultado REAL, sin forzar 196."""
        from vectrax.db import get_all_stars
        from connectors.market.ta_knowledge import known_function_names

        with patch("vectrax.engine.embed", side_effect=_fake_embed), \
             patch("vectrax.engine.find_similar") as mock_find:
            # 1ra siembra: nada existe aún -> todo nuevo.
            mock_find.return_value = []
            first = seed_module.seed_knowledge_stars()
            assert len(first["created"]) == len(known_function_names())

            # 2da siembra: mismo texto exacto -> embedding idéntico -> se
            # simula que find_similar SÍ los reconoce (>=0.95), como haría
            # el motor real con un embedding real idéntico.
            existing = get_all_stars(channel="user", owner="vectrax_system")
            by_content = {s.content: s.id for s in existing}

            def _find_similar_side_effect(vec, candidates, threshold=0.95):
                # Empareja por contenido igual al que generó `vec` -- el
                # mismo _fake_embed es determinista por texto, así que
                # basta con devolver el id cuyo embedding coincide.
                for sid, cand_vec in candidates:
                    if np.array_equal(cand_vec, vec):
                        return [(sid, 1.0)]
                return []

            mock_find.side_effect = _find_similar_side_effect
            second = seed_module.seed_knowledge_stars()

        assert len(second["created"]) == 0
        assert len(second["already_present"]) == len(known_function_names())
        # No se duplicó ninguna de las 196 PRIMARIAS -- siguen siendo
        # exactamente 196, aunque el total de filas pueda ser mayor por
        # convergencias emergentes reales (ver test de arriba).
        primary_after = [
            s for s in get_all_stars(channel="user", owner="vectrax_system")
            if s.star_type == "primary"
        ]
        assert len(primary_after) == len(known_function_names())
