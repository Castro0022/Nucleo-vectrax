"""
tests/test_convergence_counters.py

Fija la separacion entre magnitudes de convergencias que el panel y el
reporte global llegaron a confundir.

Historia del defecto, en dos actos:

  1. Antes de #107, `census.convergences` se calculaba como
     `len(gi.cross_domain_convergences())`. Total y cross-domain eran el
     mismo numero, asi que con 0 cross-domain vivas el contador superior
     mostraba 0 convergencias pese a haber miles registradas.

  2. #107 corrigio el origen: el registro canonico
     (`core.learn.convergence_registry`) paso a ser el SSOT y
     `census.convergences` quedo siendo el total real. Pero el campo que lo
     transportaba siguio llamandose `cross_domain`, de modo que la tarjeta
     "Cross-Domain Convergences" y la prosa de Telegram pasaron a afirmar
     "cross-domain" sobre una cifra que es el total. El valor era correcto;
     la etiqueta, no.

Estos tests bloquean ambos actos: que el total vuelva a derivarse del
detector de candidatos, y que el total vuelva a publicarse bajo la etiqueta
cross-domain.
"""
from __future__ import annotations

import inspect
import re
import sqlite3
from pathlib import Path

import pytest

from core.learn import convergence_registry as reg


def _statements(src: str) -> list[str]:
    """Lineas de CODIGO, sin comentarios y sin indentacion.

    Necesario porque estos archivos documentan el defecto en prosa: el censo
    lleva el comentario ``Previously `c.convergences = len(gi.cross_domain_
    convergences(...))` ``, que haria saltar cualquier busqueda ingenua de
    esa cadena.
    """
    out = []
    for line in src.splitlines():
        code = line.split("#", 1)[0].strip()
        if code:
            out.append(code)
    return out


def _assigns(src: str, target: str) -> list[str]:
    """Todo lo que se asigna a `target`, como codigo real.

    Compara la sentencia COMPLETA, no por subcadena: `x = c.convergences` es
    subcadena de `x = c.convergences_cross_domain`, y confundir ambas es
    precisamente el defecto que estos tests vigilan.
    """
    pat = re.compile(rf"^{re.escape(target)}\s*=\s*(.+)$")
    return [m.group(1).strip() for m in (pat.match(s) for s in _statements(src)) if m]


# ===========================================================================
# 1. Registro canonico: total y cross-domain son conteos DISTINTOS
# ===========================================================================

@pytest.fixture()
def registry_db(tmp_path) -> str:
    """Registro con 5 convergencias: 2 cruzan dominios, 3 no."""
    db = tmp_path / "convergence_history.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(reg.SCHEMA)
    rows = [
        # (id, a, b, domain_a, domain_b, status)
        ("c1", "x1", "y1", "market", "freight_logistics", "active"),
        ("c2", "x2", "y2", "market", "cybersecurity", "active"),
        ("c3", "x3", "y3", "market", "market", "active"),
        ("c4", "x4", "y4", "freight_logistics", "freight_logistics", "dissolved"),
        # Dominio sin resolver: no es evidencia de cruce.
        ("c5", "x5", "y5", "market", "unknown_legacy", "active"),
    ]
    for cid, a, b, da, db_, status in rows:
        conn.execute(
            "INSERT INTO convergences (convergence_id, entity_a_id, entity_a_type,"
            " entity_b_id, entity_b_type, domain_a, domain_b, first_seen,"
            " last_seen, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cid, a, "star", b, "star", da, db_, 0.0, 0.0, status),
        )
    conn.commit()
    conn.close()
    return str(db)


class TestRegistryCountsAreDistinct:

    def test_total_counts_every_convergence(self, registry_db):
        assert reg.count_canonical_convergences(db_path=registry_db) == 5

    def test_cross_domain_counts_only_real_crossings(self, registry_db):
        """c1 y c2 cruzan. c3/c4 son mismo dominio. c5 tiene un lado sin
        resolver y no cuenta como cruce verificado."""
        assert reg.count_cross_domain_convergences(db_path=registry_db) == 2

    def test_cross_domain_is_a_strict_subset_of_total(self, registry_db):
        total = reg.count_canonical_convergences(db_path=registry_db)
        cross = reg.count_cross_domain_convergences(db_path=registry_db)
        assert cross < total, "cross-domain nunca puede igualar al total aqui"

    def test_cross_domain_respects_status_filter(self, registry_db):
        assert reg.count_cross_domain_convergences(
            status="active", db_path=registry_db,
        ) == 2
        assert reg.count_cross_domain_convergences(
            status="dissolved", db_path=registry_db,
        ) == 0

    def test_unknown_domains_excluded_from_both_sides(self, tmp_path):
        db = tmp_path / "only_unknown.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(reg.SCHEMA)
        conn.execute(
            "INSERT INTO convergences (convergence_id, entity_a_id, entity_a_type,"
            " entity_b_id, entity_b_type, domain_a, domain_b, first_seen,"
            " last_seen, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("u1", "a", "star", "b", "star", "", "unknown_legacy", 0.0, 0.0, "active"),
        )
        conn.commit()
        conn.close()
        assert reg.count_canonical_convergences(db_path=str(db)) == 1
        assert reg.count_cross_domain_convergences(db_path=str(db)) == 0


# ===========================================================================
# 2. Censo: expone ambas magnitudes, y el total NO vuelve al detector
# ===========================================================================

class TestCensusExposesBoth:

    def test_census_has_cross_domain_field(self):
        from core.universe_census import UniverseCensus
        c = UniverseCensus()
        assert hasattr(c, "convergences_cross_domain")
        c.convergences = 5604
        c.convergences_cross_domain = 2
        d = c.to_dict()
        assert d["convergences"] == 5604
        assert d["convergences_cross_domain"] == 2

    def test_total_never_derives_from_the_candidate_detector(self):
        """Regresion del acto 1: el detector de gravity_engine no puede
        volver a ser la fuente del total."""
        import core.universe_census as uc
        assigned = _assigns(inspect.getsource(uc), "c.convergences")
        assert assigned == ["count_canonical_convergences()"], assigned

    def test_cross_domain_reads_the_registry(self):
        import core.universe_census as uc
        assigned = _assigns(
            inspect.getsource(uc), "c.convergences_cross_domain",
        )
        assert assigned == ["count_cross_domain_convergences()"], assigned


# ===========================================================================
# 3. Dashboard y reporte global: cada etiqueta con su cifra
# ===========================================================================

def _src_of(module_path: str, attr: str) -> str:
    mod = __import__(module_path, fromlist=[attr.split(".")[0]])
    return inspect.getsource(getattr(mod, attr))


class TestBackendLabelsMatchTheirNumbers:

    def test_dashboard_separates_total_from_cross_domain(self):
        src = _src_of("services.core.routes.dashboard", "dashboard_observatory")
        assert _assigns(src, 'convergences["total"]') == ["census.convergences"]
        assert _assigns(src, 'convergences["cross_domain"]') == [
            "census.convergences_cross_domain"
        ], "Regresion del acto 2: cross_domain vuelve a llevar el total"

    def test_global_state_separates_them(self):
        src = _src_of("core.system_report", "get_global_state")
        assert _assigns(src, 'convergences["total"]') == ["c.convergences"]
        assert _assigns(src, 'convergences["cross_domain"]') == [
            "c.convergences_cross_domain"
        ]


# ===========================================================================
# 4. Frontend: ningun rotulo puede volver a mentir
# ===========================================================================

_STATIC = Path(__file__).resolve().parents[1] / "services" / "ui" / "static"


class TestFrontendCountersNotSwapped:

    def _read(self, name: str) -> str:
        return (_STATIC / name).read_text(encoding="utf-8")

    def test_top_counter_reads_total(self):
        js = self._read("app.js")
        assert "metric('🔗 Convergences', (obs.convergences || {}).total ?? 0)" in js
        assert (
            "metric('🔗 Convergences', (obs.convergences || {}).cross_domain ?? 0)"
            not in js
        ), "Regresion: el contador superior vuelve a leer cross_domain"

    def test_cross_domain_card_reads_cross_domain(self):
        js = self._read("app.js")
        assert "🔗 Cross-Domain Convergences" in js
        card = js.split("🔗 Cross-Domain Convergences", 1)[1][:400]
        assert "conv.cross_domain" in card, (
            "La tarjeta cross-domain debe mostrar la cifra cross-domain"
        )

    def test_observatory_card_no_longer_calls_the_total_cross_domain(self):
        html = self._read("observatory.html")
        assert "metric('Convergencias activas', fmt(c.cross_domain), 'accent')" not in html, (
            "Regresion: la tarjeta etiquetaba el total como cross-domain Y como activas"
        )
        assert "metric('Cruzan dos dominios', fmt(c.cross_domain))" in html
