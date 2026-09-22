"""
tests/test_causal_evidence_answers.py — Lo que el Núcleo puede AFIRMAR.

LA REGLA DE LENGUAJE
--------------------
"Aprendí" solo puede decirse cuando existe un `LearningTrace` en estado
LEARNED. Es el invariante central de Mario:

    "Vectrax solo puede afirmar 'Lo aprendí porque convergió' si existe una
     cadena persistida y reconstruible."

Una observación repetida, un patrón, una estrella madura o una convergencia
reescaneada NO equivalen por sí solos a aprendizaje. Estas pruebas comprueban
las dos direcciones:

  * con un aprendizaje LEARNED, el Núcleo dice "Aprendí …" y nombra la
    convergencia y los umbrales que se cruzaron;
  * sin él, NO lo dice en ninguna formulación, y explica qué falta.

Y que toda la traza causal es owner-only: un desconocido no obtiene nada.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.learn import causal_learning as cl  # noqa: E402
from core.nucleus.evidence_intent import build_answer  # noqa: E402
from core.nucleus.internal_evidence import (  # noqa: E402
    EvidenceAccess, EvidenceStatus, InternalEvidence,
)

OWNER = EvidenceAccess(owner_canonical="mario", is_owner=True, owner_raw="mario")
STRANGER = EvidenceAccess(owner_canonical="ana", is_owner=False, owner_raw="tg:999")

STRONG = {"win_rate": 0.80, "expectancy": 0.60, "confidence": 1.0, "sample_size": 20.0}
THIN = {"win_rate": 1.00, "expectancy": 1.00, "confidence": 0.25, "sample_size": 5.0}

#: Formulaciones que NO pueden aparecer sin un LearningTrace en LEARNED.
_CLAIM_WORDS = ("aprendí", "aprendido", "he aprendido", "lo aprendí")


def _fetcher(**by_fp):
    def _fetch(fp):
        return by_fp.get(fp)
    return _fetch


BOTH_STRONG = _fetcher(**{"market:AAPL": STRONG, "freight:LANE-7": STRONG})


def _snapshot(**kw):
    base = dict(
        convergence_id="CONV-MKT-FRT", domain="market",
        source_pattern_ids=["market:AAPL", "freight:LANE-7"],
        evidence_ids=["EV-1", "EV-2"],
        metrics={"combined_hits": 9.0, "combined_cc": 0.77},
        claim="market:AAPL converge con freight:LANE-7",
    )
    base.update(kw)
    return cl.CausalSnapshot(**base)


@pytest.fixture
def clean_sources(monkeypatch):
    import core.domain_knowledge as dk
    import core.learn.gravity_engine as ge
    import core.learn.verification_ledger as vl
    monkeypatch.setattr(dk, "get_domain_priors", lambda domain: [], raising=False)
    monkeypatch.setattr(
        ge, "get_gravity_index",
        lambda: type("_GI", (), {"by_domain": lambda self, d: []})(), raising=False,
    )
    monkeypatch.setattr(
        vl, "subject_scores", lambda domain, min_decisive=1: {}, raising=False,
    )


@pytest.fixture
def learned(clean_sources):
    cl.ensure_production_policies()
    d = cl.evaluate_convergence(_snapshot(), stats_fetcher=BOTH_STRONG)
    assert d.state == cl.STATE_LEARNED, d.reasons
    return d


@pytest.fixture
def candidate(clean_sources):
    cl.ensure_production_policies()
    d = cl.evaluate_convergence(
        _snapshot(),
        stats_fetcher=_fetcher(**{"market:AAPL": STRONG, "freight:LANE-7": THIN}),
    )
    assert d.state == cl.STATE_CONVERGED_CANDIDATE
    return d


# ===========================================================================
# "Aprendí esto porque la convergencia X superó estos umbrales."
# ===========================================================================

class TestLearnedAnswer:

    def test_the_nucleus_says_it_learned(self, learned):
        result = InternalEvidence(OWNER).causal_learnings("market")
        assert result.status in (EvidenceStatus.OK, EvidenceStatus.STALE)
        answer = build_answer(result).lower()
        assert "aprendí" in answer

    def test_it_names_the_convergence_and_the_thresholds(self, learned):
        item = InternalEvidence(OWNER).causal_learnings("market").items[0]
        assert "CONV-MKT-FRT" in item.summary
        assert "umbral" in item.summary
        assert "win_rate" in item.summary

    def test_the_answer_is_reconstructible_from_ids(self, learned):
        item = InternalEvidence(OWNER).causal_learnings("market").items[0]
        assert item.reference == learned.learning_id
        assert item.data["convergence_id"] == "CONV-MKT-FRT"
        assert item.data["source_pattern_ids"] == ["market:AAPL", "freight:LANE-7"]
        assert item.data["policy_id"] == "production-market"
        assert item.data["thresholds_at_promotion"]["min_win_rate"] == 55.0

    def test_it_is_dated_by_the_source_not_by_now(self, learned):
        item = InternalEvidence(OWNER).causal_learnings("market").items[0]
        stored = cl.get_learning(learned.learning_id)["learned_at"]
        assert item.observed_at == stored


# ===========================================================================
# Sin LearningTrace en LEARNED, NO se puede decir "aprendí"
# ===========================================================================

class TestWithoutLearnedItNeverClaims:

    def test_a_candidate_never_produces_a_learning_claim(self, candidate):
        ev = InternalEvidence(OWNER)
        learned_answer = build_answer(ev.causal_learnings("market")).lower()
        for word in _CLAIM_WORDS:
            assert word not in learned_answer, (
                f"afirmó {word!r} sin un LearningTrace en LEARNED:\n{learned_answer}"
            )

    def test_a_candidate_says_what_is_missing(self, candidate):
        result = InternalEvidence(OWNER).causal_candidates("market")
        assert result.items
        summary = result.items[0].summary
        assert "todavía no supera el umbral" in summary
        assert "sample_size=5" in summary
        for word in _CLAIM_WORDS:
            assert word not in summary.lower()

    def test_an_empty_store_says_so_plainly(self, clean_sources):
        result = InternalEvidence(OWNER).causal_learnings("market")
        assert result.status is EvidenceStatus.EMPTY
        assert "todavía no hay nada que afirmar" in result.detail
        # Ni siquiera negado: el guard prohíbe el literal.
        for word in _CLAIM_WORDS:
            assert word not in result.detail.lower()
        assert result.items == []

    def test_a_repeated_scan_never_becomes_a_claim(self, clean_sources):
        """Cien confirmaciones de una convergencia floja siguen sin ser aprendizaje."""
        cl.ensure_production_policies()
        thin = _fetcher(**{"market:AAPL": STRONG, "freight:LANE-7": THIN})
        for i in range(100):
            cl.evaluate_convergence(
                _snapshot(metrics={
                    "combined_hits": 9.0, "combined_cc": 0.77,
                    "confirmation_count": i,
                }),
                stats_fetcher=thin,
            )
        answer = build_answer(InternalEvidence(OWNER).causal_learnings("market")).lower()
        for word in _CLAIM_WORDS:
            assert word not in answer

    def test_a_weakened_learning_stops_being_claimed(self, learned):
        cl.evaluate_convergence(
            _snapshot(status="dissolved", lifecycle_event="dissolved"),
            stats_fetcher=BOTH_STRONG,
        )
        result = InternalEvidence(OWNER).causal_learnings("market")
        assert result.status is EvidenceStatus.EMPTY


# ===========================================================================
# "Este aprendizaje cambió este criterio."
# ===========================================================================

class TestCriterionChangeAnswer:

    def test_it_names_the_learning_and_the_convergence(self, learned):
        item = InternalEvidence(OWNER).criterion_change("market").items[0]
        assert learned.learning_id in item.summary
        assert "CONV-MKT-FRT" in item.summary
        assert item.data["previous_criterion"] == []
        assert item.data["effective_criterion"]

    def test_without_learning_there_is_no_claimed_change(self, candidate):
        result = InternalEvidence(OWNER).criterion_change("market")
        assert result.status is EvidenceStatus.EMPTY
        assert "sin aprendizaje no puedo afirmar un cambio" in result.detail

    def test_a_missing_domain_is_reported_not_guessed(self, clean_sources):
        result = InternalEvidence(OWNER).criterion_change("")
        assert result.status is EvidenceStatus.EMPTY
        assert "no se indicó dominio" in result.detail


# ===========================================================================
# "Se aplicó o me abstuve aquí" + "el resultado fue este"
# ===========================================================================

class TestApplicationAnswer:

    def test_an_application_with_a_favourable_outcome_reinforces(self, learned):
        app = cl.record_application(
            learned.learning_id, convergence_id="CONV-MKT-FRT",
            decision_id="DEC-1", criterion_version="crit-abc",
        )
        cl.record_outcome(app, outcome_status="win", outcome_value=1.5)
        item = InternalEvidence(OWNER).causal_applications(learned.learning_id).items[0]
        assert "se aplicó en la decisión DEC-1" in item.summary
        assert "lo reforzó" in item.summary

    def test_a_contradicting_outcome_is_named_as_such(self, learned):
        app = cl.record_application(learned.learning_id, decision_id="DEC-2")
        cl.record_outcome(app, outcome_status="loss", outcome_value=-0.8)
        item = InternalEvidence(OWNER).causal_applications(learned.learning_id).items[0]
        assert "lo contradijo" in item.summary

    def test_an_abstention_is_reported_as_an_abstention(self, learned):
        cl.record_application(
            learned.learning_id, decision_id="DEC-3", applied=False,
            abstained_reason="governor en pausa",
        )
        item = InternalEvidence(OWNER).causal_applications(learned.learning_id).items[0]
        assert "me abstuve" in item.summary
        assert "governor en pausa" in item.summary
        assert item.status == "abstained"

    def test_a_pending_outcome_is_not_invented(self, learned):
        cl.record_application(learned.learning_id, decision_id="DEC-4")
        item = InternalEvidence(OWNER).causal_applications(learned.learning_id).items[0]
        assert "todavía sin resultado registrado" in item.summary

    def test_a_domain_without_an_executor_says_so(self, learned):
        """No es "todavía nada": es que nadie puede aplicarlo."""
        result = InternalEvidence(OWNER).causal_applications(domain="cybersecurity")
        assert result.status is EvidenceStatus.EMPTY
        assert "NO_OPERATIONAL_CONSUMER" in result.detail
        assert "ningún ejecutor consume su criterio" in result.detail

    def test_market_does_have_an_executor(self, learned):
        result = InternalEvidence(OWNER).causal_applications(domain="market")
        assert "NO_OPERATIONAL_CONSUMER" not in (result.detail or "")

    def test_nothing_applied_says_so(self, learned):
        result = InternalEvidence(OWNER).causal_applications(learned.learning_id)
        assert result.status is EvidenceStatus.EMPTY
        assert "ningún aprendizaje ha influido" in result.detail


# ===========================================================================
# La traza causal es owner-only
# ===========================================================================

class TestOwnerOnly:

    @pytest.mark.parametrize("method,args", [
        ("causal_learnings", ("market",)),
        ("causal_candidates", ("market",)),
        ("criterion_change", ("market",)),
        ("causal_applications", ("",)),
    ])
    def test_a_stranger_gets_nothing(self, method, args, learned):
        result = getattr(InternalEvidence(STRANGER), method)(*args)
        assert result.status is EvidenceStatus.UNAUTHORIZED
        assert result.items == []
        answer = build_answer(result).lower()
        for word in _CLAIM_WORDS:
            assert word not in answer
        assert "CONV-MKT-FRT" not in build_answer(result)

    def test_the_kinds_are_registered_as_owner_only(self):
        from core.nucleus.internal_evidence import _OWNER_ONLY
        for kind in ("causal_learning", "causal_candidate", "criterion_change",
                     "causal_application"):
            assert kind in _OWNER_ONLY


# ===========================================================================
# Fallo del almacén: se dice, no se inventa
# ===========================================================================

class TestStoreFailureIsReported:

    def test_an_unavailable_store_is_reported_not_silenced(self, monkeypatch):
        import core.learn.causal_learning as _cl

        def _explode(*a, **kw):
            raise RuntimeError("almacén causal caído")

        monkeypatch.setattr(_cl, "list_learnings", _explode)
        result = InternalEvidence(OWNER).causal_learnings("market")
        assert result.status is EvidenceStatus.UNAVAILABLE
        assert "almacén causal caído" in result.detail
        answer = build_answer(result).lower()
        for word in _CLAIM_WORDS:
            assert word not in answer
