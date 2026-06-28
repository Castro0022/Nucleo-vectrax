"""
Guard invariante — Las 7 Leyes NO son contenido del universo.

Las leyes son sesgo de decisión pre-universo / gravedad de selección. Este test
falla si alguna vez se filtran como estrellas/patrones/fenómenos del universo:

  1. Estructural: law_enforcement no debe referenciar los almacenes del universo.
  2. QUALITY_DOMAINS (lo que el universo materializa) no incluye dominios de ley.
  3. Funcional: una observación con forma de violación de ley nunca se
     materializa como entidad de calidad del universo.

Run:
    pytest tests/test_laws_not_universe_content.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def test_law_enforcement_does_not_touch_universe_stores():
    """Las leyes registran en el audit ledger, nunca en los stores del universo."""
    src = Path(_ROOT, "core/operator/law_enforcement.py").read_text(encoding="utf-8")
    for forbidden in (
        "observation_ledger",
        "gravity_engine",
        "get_gravity_index",
        "quality_entities",
    ):
        assert forbidden not in src, (
            f"law_enforcement NO debe referenciar {forbidden!r}: las 7 Leyes son "
            "sesgo de selección, no contenido del universo."
        )


def test_quality_domains_exclude_laws():
    """Los dominios que el universo materializa no incluyen leyes/razonamiento."""
    from core.self_observation.quality_entities import QUALITY_DOMAINS
    for d in QUALITY_DOMAINS:
        dl = str(d).lower()
        assert "law" not in dl and "ley" not in dl and "reasoning" not in dl


def test_law_violation_row_not_materialized_as_universe_entity(monkeypatch):
    """Una fila con forma de violación de ley jamás se vuelve entidad de calidad."""
    from core.self_observation import observation_ledger as ol
    from core.self_observation import quality_entities as qe

    fake_rows = [
        {  # forma de violación de ley (dominio de auditoría) — debe excluirse
            "id": 1, "domain": "operator.reasoning",
            "obs_type": "law_violation:L3:Vibracion", "summary": "L3 violated",
            "severity": "warning", "evidence": None, "timestamp": "t", "star_id": None,
        },
        {  # fallo de calidad genuino — debe incluirse
            "id": 2, "domain": "quality", "obs_type": "test_failure",
            "summary": "real boom", "severity": "critical", "evidence": None,
            "timestamp": "t", "star_id": None,
        },
    ]
    monkeypatch.setattr(ol, "get_recent", lambda limit=400: list(fake_rows))

    ents = qe.get_quality_entities(limit=400)
    all_ents = (
        ents["failures"] + ents["recoveries"] + ents["events"] + ents["convergences"]
    )

    # Ninguna ley se filtra al universo.
    assert all("law_violation" not in str(e.get("obs_type", "")) for e in all_ents)
    assert all(
        str(e.get("domain", "")).lower() != "operator.reasoning" for e in all_ents
    )
    # El fallo de calidad genuino sí se materializa.
    assert any(e.get("severity") == "critical" for e in ents["failures"])
