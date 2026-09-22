"""
tests/test_causal_learning_store.py — El gate causal y su almacén.

QUÉ PROTEGE
-----------
`core/learn/causal_learning.py` es la SSOT del puente
`patrón → convergencia → aprendizaje → criterio`, EFECTIVO en producción. Su
valor entero depende de tres propiedades fáciles de romper sin darse cuenta:

1. **Producción directa NO es promoción automática.** Una convergencia solo
   llega a `LEARNED` si supera el gate completo. El incidente de las 121.206
   convergencias demuestra que el volumen del escáner no es evidencia.

2. **Una repetición no es evidencia.** `convergence_registry` incrementa
   `confirmation_count` en cada re-escaneo. Si eso contara, bastaría con dejar
   el escáner corriendo para que cualquier convergencia "aprendiera" sola. La
   prueba bloqueante es `test_one_hundred_identical_snapshots_do_not_duplicate`.

3. **La identidad causal se persiste y se puede reconstruir.** Un aprendizaje
   sin `source_convergence_id`, sin `policy_id` o sin `evidence_revision_hash`
   no es auditable: es una afirmación.

AISLAMIENTO
-----------
Ninguna prueba escribe en el vault real: todas pasan un `db_path` bajo
`tmp_path`, y las métricas de patrón se inyectan con un doble en vez de leer
el gravity index vivo.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.learn import causal_learning as cl  # noqa: E402


@pytest.fixture
def db(tmp_path) -> str:
    """Ruta del almacén bajo tmp. El archivo aún NO existe."""
    return str(tmp_path / "causal" / "causal_learning.db")


#: Métricas de un patrón que SÍ cualifica: 20 outcomes graduados, 80% de
#: aciertos, expectancy positiva. Es la forma exacta que devuelve
#: `core.gravity_kernel.signals.fetch_pattern_stats`.
STRONG = {"win_rate": 0.80, "expectancy": 0.60, "confidence": 1.0, "sample_size": 20.0}
#: Muestra insuficiente (5 < MIN_SAMPLE=15) aunque el acierto sea perfecto.
THIN = {"win_rate": 1.00, "expectancy": 1.00, "confidence": 0.25, "sample_size": 5.0}
#: Muestra suficiente pero acierto por debajo de MIN_WIN_RATE=55%.
WEAK = {"win_rate": 0.40, "expectancy": -0.20, "confidence": 1.0, "sample_size": 20.0}


def _fetcher(**by_fingerprint):
    """Doble de `fetch_pattern_stats`: métricas reales inyectadas, sin gravity index."""
    def _fetch(fingerprint):
        return by_fingerprint.get(fingerprint)
    return _fetch


BOTH_STRONG = _fetcher(**{"PAT-A": STRONG, "PAT-B": STRONG})


def _policy(domain: str = "market", **kw) -> cl.LearningPolicy:
    """Política con la MISMA forma que la de producción."""
    base = cl.build_production_policy(domain)
    if not kw:
        return base
    thresholds = dict(base.thresholds)
    thresholds.update(kw.pop("thresholds", {}))
    return cl.LearningPolicy(
        policy_id=kw.pop("policy_id", base.policy_id),
        domain=domain,
        thresholds=thresholds,
        required_metrics=base.required_metrics,
        policy_version=kw.pop("policy_version", base.policy_version),
        threshold_provenance=base.threshold_provenance,
        **kw,
    )


def _snapshot(**kw) -> cl.CausalSnapshot:
    base = dict(
        convergence_id="CONV-001",
        domain="market",
        source_pattern_ids=["PAT-A", "PAT-B"],
        evidence_ids=["EV-1", "EV-2"],
        metrics={"combined_hits": 9.0, "combined_cc": 0.77},
        claim="A y B convergen en market",
        data_scope="market/live",
    )
    base.update(kw)
    return cl.CausalSnapshot(**base)


def _learn(db, snapshot=None, fetcher=BOTH_STRONG):
    """Atajo: política de producción + una convergencia que supera el gate."""
    cl.register_policy(_policy(), db_path=db)
    return cl.evaluate_convergence(
        snapshot or _snapshot(), stats_fetcher=fetcher, db_path=db,
    )


# ===========================================================================
# NO EXISTE MODO SOMBRA
# ===========================================================================

class TestNoShadowMode:
    """Mario no autorizó un modo sombra. No puede quedar ni un resto."""

    def test_the_module_exposes_no_mode_at_all(self):
        for attr in ("MODE_SHADOW", "MODE_ENFORCE", "MODES", "DEFAULT_MODE"):
            assert not hasattr(cl, attr), f"quedó un vestigio de modo: {attr}"

    def test_there_is_no_eligible_shadow_state(self):
        assert not hasattr(cl, "STATE_ELIGIBLE_SHADOW")
        assert "ELIGIBLE_SHADOW" not in cl.STATES

    def test_learned_is_the_only_consumable_state(self):
        assert cl.CONSUMABLE_STATES == (cl.STATE_LEARNED,)

    def test_no_environment_variable_can_defer_the_effect(self, db, monkeypatch):
        """Ninguna variable puede dejar el aprendizaje sin efecto."""
        for var in ("VECTRAX_CAUSAL_SHADOW", "VECTRAX_LEARNING_SHADOW",
                    "VECTRAX_CAUSAL_MODE", "VECTRAX_LEARNING_MODE"):
            monkeypatch.setenv(var, "shadow")
        d = _learn(db)
        assert d.state == cl.STATE_LEARNED, (
            "una variable de entorno logró posponer el efecto del aprendizaje"
        )

    def test_a_policy_cannot_carry_a_mode(self):
        with pytest.raises(TypeError):
            cl.LearningPolicy(
                policy_id="p", domain="market", thresholds={},
                threshold_provenance={}, mode="shadow",
            )


# ===========================================================================
# EL GATE: una convergencia cualificada aprende; una insuficiente NO
# ===========================================================================

class TestTheGate:

    def test_a_qualified_convergence_becomes_learned(self, db):
        d = _learn(db)
        assert d.state == cl.STATE_LEARNED
        assert d.eligible is True
        learning = cl.get_learning(d.learning_id, db_path=db)
        assert learning["state"] == cl.STATE_LEARNED
        assert learning["learned_at"] is not None

    def test_without_convergence_there_is_no_learning(self, db):
        cl.register_policy(_policy(), db_path=db)
        d = cl.evaluate_convergence(
            _snapshot(convergence_id=""), stats_fetcher=BOTH_STRONG, db_path=db,
        )
        assert d.state != cl.STATE_LEARNED
        assert cl.list_learnings(state=cl.STATE_LEARNED, db_path=db) == []

    def test_a_domain_without_policy_stays_awaiting_policy(self, db):
        d = cl.evaluate_convergence(
            _snapshot(domain="restaurant"), stats_fetcher=BOTH_STRONG, db_path=db,
        )
        assert d.state == cl.STATE_AWAITING_POLICY
        assert "no existe una política" in " ".join(d.reasons)

    def test_a_thin_pattern_keeps_it_a_candidate(self, db):
        """Muestra insuficiente: candidata, NO aprendida — y no por sombra."""
        cl.register_policy(_policy(), db_path=db)
        d = cl.evaluate_convergence(
            _snapshot(), stats_fetcher=_fetcher(**{"PAT-A": STRONG, "PAT-B": THIN}),
            db_path=db,
        )
        assert d.state == cl.STATE_CONVERGED_CANDIDATE
        assert any("sample_size=5" in r for r in d.reasons)

    def test_a_weak_pattern_keeps_it_a_candidate(self, db):
        cl.register_policy(_policy(), db_path=db)
        d = cl.evaluate_convergence(
            _snapshot(), stats_fetcher=_fetcher(**{"PAT-A": STRONG, "PAT-B": WEAK}),
            db_path=db,
        )
        assert d.state == cl.STATE_CONVERGED_CANDIDATE
        assert any("win_rate=40.0%" in r for r in d.reasons)

    def test_a_pattern_without_real_metrics_never_qualifies(self, db):
        """Sin outcomes graduados no hay métricas: no se suponen."""
        cl.register_policy(_policy(), db_path=db)
        d = cl.evaluate_convergence(
            _snapshot(), stats_fetcher=_fetcher(**{"PAT-A": STRONG}), db_path=db,
        )
        assert d.state == cl.STATE_CONVERGED_CANDIDATE
        assert any("no tiene outcomes graduados" in r for r in d.reasons)

    def test_both_source_patterns_are_required(self, db):
        cl.register_policy(_policy(), db_path=db)
        d = cl.evaluate_convergence(
            _snapshot(source_pattern_ids=["PAT-A"]),
            stats_fetcher=BOTH_STRONG, db_path=db,
        )
        assert d.state == cl.STATE_CONVERGED_CANDIDATE
        assert any("dos patrones fuente" in r for r in d.reasons)

    def test_a_weak_convergence_stays_a_candidate(self, db):
        """Patrones fuertes pero convergencia por debajo de ALERT_MIN_CC."""
        cl.register_policy(_policy(), db_path=db)
        d = cl.evaluate_convergence(
            _snapshot(metrics={"combined_hits": 9.0, "combined_cc": 0.10}),
            stats_fetcher=BOTH_STRONG, db_path=db,
        )
        assert d.state == cl.STATE_CONVERGED_CANDIDATE
        assert any("combined_cc" in r for r in d.reasons)

    def test_a_candidate_always_says_what_it_is_missing(self, db):
        cl.register_policy(_policy(), db_path=db)
        d = cl.evaluate_convergence(
            _snapshot(), stats_fetcher=_fetcher(**{"PAT-A": STRONG, "PAT-B": THIN}),
            db_path=db,
        )
        assert d.reasons, "un candidato sin razones no es auditable"
        assert all(isinstance(r, str) and r for r in d.reasons)

    def test_the_first_live_cycle_can_learn(self, db):
        """No hay espera artificial: con evidencia suficiente, aprende ya."""
        d = _learn(db)
        assert d.state == cl.STATE_LEARNED
        assert cl.count_revisions("CONV-001", db_path=db) == 1, (
            "aprendió en la PRIMERA revisión, sin periodo de gracia"
        )

    def test_confidence_comes_from_the_weakest_pattern(self, db):
        cl.register_policy(_policy(), db_path=db)
        weakish = dict(STRONG, confidence=0.5)
        d = cl.evaluate_convergence(
            _snapshot(), stats_fetcher=_fetcher(**{"PAT-A": STRONG, "PAT-B": weakish}),
            db_path=db,
        )
        assert d.state == cl.STATE_LEARNED
        assert cl.get_learning(d.learning_id, db_path=db)["confidence"] == 0.5


# ===========================================================================
# EL GUARD PRINCIPAL: repetir un escaneo no es evidencia nueva
# ===========================================================================

class TestFalseRepetition:

    def test_one_hundred_identical_snapshots_do_not_duplicate(self, db):
        """PRUEBA BLOQUEANTE.

        Invariante de Mario: "una convergencia reescaneada no equivale por sí
        sola a aprendizaje". El escáner puede confirmar la misma convergencia
        miles de veces; si eso contara, bastaría con esperar.
        """
        cl.register_policy(_policy(), db_path=db)
        snap = _snapshot()
        decisions = [
            cl.evaluate_convergence(snap, stats_fetcher=BOTH_STRONG, db_path=db)
            for _ in range(100)
        ]

        assert cl.count_revisions("CONV-001", db_path=db) == 1
        assert len(cl.get_decisions(convergence_id="CONV-001", db_path=db)) == 1
        # Todas devuelven la MISMA decisión; solo la primera la creó.
        assert len({d.evaluation_id for d in decisions}) == 1
        assert decisions[0].reused_existing_revision is False
        assert all(d.reused_existing_revision for d in decisions[1:])
        # Y UN aprendizaje, no cien.
        assert len({d.learning_id for d in decisions}) == 1
        assert len(cl.list_learnings(db_path=db)) == 1

    def test_repetition_does_not_raise_confidence(self, db):
        cl.register_policy(_policy(), db_path=db)
        snap = _snapshot()
        first = cl.evaluate_convergence(snap, stats_fetcher=BOTH_STRONG, db_path=db)
        before = cl.get_learning(first.learning_id, db_path=db)["confidence"]
        for _ in range(50):
            cl.evaluate_convergence(snap, stats_fetcher=BOTH_STRONG, db_path=db)
        after = cl.get_learning(first.learning_id, db_path=db)["confidence"]
        assert after == before, "reejecutar el mismo snapshot aumentó la confianza"

    def test_confirmation_count_alone_never_promotes(self, db):
        """Un candidato no se vuelve aprendizaje a fuerza de confirmaciones."""
        cl.register_policy(_policy(), db_path=db)
        thin = _fetcher(**{"PAT-A": STRONG, "PAT-B": THIN})
        for count in range(1, 51):
            d = cl.evaluate_convergence(
                _snapshot(metrics={
                    "combined_hits": 9.0, "combined_cc": 0.77,
                    "confirmation_count": count,      # <- lo único que cambia
                    "last_seen": 1_700_000_000 + count,
                }),
                stats_fetcher=thin, db_path=db,
            )
        assert d.state == cl.STATE_CONVERGED_CANDIDATE
        assert cl.count_revisions("CONV-001", db_path=db) == 1
        assert cl.list_learnings(state=cl.STATE_LEARNED, db_path=db) == []

    def test_a_maturing_pattern_creates_a_revision_and_can_promote(self, db):
        """El hueco que cerraba esto: un candidato rechazado por muestra
        insuficiente debe poder promover cuando el patrón madura.

        Si las métricas del patrón no entraran en el hash, el escaneo repetido
        produciría SIEMPRE la misma revisión y la convergencia no se volvería a
        evaluar jamás, ni siquiera con el patrón ya cualificado.
        """
        cl.register_policy(_policy(), db_path=db)
        snap = _snapshot()

        # Ciclo 1: PAT-B solo tiene 5 outcomes -> candidata.
        first = cl.evaluate_convergence(
            snap, stats_fetcher=_fetcher(**{"PAT-A": STRONG, "PAT-B": THIN}),
            db_path=db,
        )
        assert first.state == cl.STATE_CONVERGED_CANDIDATE

        # Ciclo N: el MISMO snapshot, pero PAT-B ya acumuló outcomes reales.
        second = cl.evaluate_convergence(
            snap, stats_fetcher=BOTH_STRONG, db_path=db,
        )
        assert second.state == cl.STATE_LEARNED, (
            "la maduración del patrón no produjo una revisión nueva"
        )
        assert cl.count_revisions("CONV-001", db_path=db) == 2
        assert second.learning_id == first.learning_id, "debe ser EL MISMO aprendizaje"

    def test_substantive_evidence_change_does_create_a_revision(self, db):
        cl.register_policy(_policy(), db_path=db)
        cl.evaluate_convergence(_snapshot(), stats_fetcher=BOTH_STRONG, db_path=db)
        cl.evaluate_convergence(
            _snapshot(evidence_ids=["EV-1", "EV-2", "EV-3"]),
            stats_fetcher=BOTH_STRONG, db_path=db,
        )
        assert cl.count_revisions("CONV-001", db_path=db) == 2

    def test_metric_movement_creates_a_revision(self, db):
        cl.register_policy(_policy(), db_path=db)
        cl.evaluate_convergence(_snapshot(), stats_fetcher=BOTH_STRONG, db_path=db)
        cl.evaluate_convergence(
            _snapshot(metrics={"combined_hits": 11.0, "combined_cc": 0.77}),
            stats_fetcher=BOTH_STRONG, db_path=db,
        )
        assert cl.count_revisions("CONV-001", db_path=db) == 2

    def test_hash_ignores_ordering_of_ids(self):
        a = cl.compute_evidence_revision_hash(
            _snapshot(source_pattern_ids=["PAT-A", "PAT-B"], evidence_ids=["EV-1", "EV-2"])
        )
        b = cl.compute_evidence_revision_hash(
            _snapshot(source_pattern_ids=["PAT-B", "PAT-A"], evidence_ids=["EV-2", "EV-1"])
        )
        assert a == b, "el orden de un conjunto no es evidencia distinta"

    def test_hash_ignores_float_noise(self):
        a = cl.compute_evidence_revision_hash(_snapshot(metrics={"combined_cc": 0.7700000001}))
        b = cl.compute_evidence_revision_hash(_snapshot(metrics={"combined_cc": 0.77000000009}))
        assert a == b

    def test_every_non_evidential_key_is_excluded(self):
        base = cl.compute_evidence_revision_hash(_snapshot(metrics={"combined_cc": 0.5}))
        for key in cl._NON_EVIDENTIAL_METRIC_KEYS:
            noisy = cl.compute_evidence_revision_hash(
                _snapshot(metrics={"combined_cc": 0.5, key: 999999})
            )
            assert noisy == base, f"la clave no evidencial {key!r} alteró el hash"

    def test_different_convergences_never_share_a_revision(self):
        a = cl.compute_evidence_revision_hash(_snapshot(convergence_id="CONV-001"))
        b = cl.compute_evidence_revision_hash(_snapshot(convergence_id="CONV-002"))
        assert a != b


# ===========================================================================
# Ciclo de vida: refuerzo, debilitamiento, contradicción
# ===========================================================================

class TestLifecycle:

    def test_a_dissolved_convergence_weakens_without_erasing(self, db):
        d = _learn(db)
        learning_before = cl.get_learning(d.learning_id, db_path=db)
        weakened = cl.evaluate_convergence(
            _snapshot(status="dissolved", lifecycle_event="dissolved"),
            stats_fetcher=BOTH_STRONG, db_path=db,
        )
        assert weakened.state == cl.STATE_WEAKENED
        after = cl.get_learning(d.learning_id, db_path=db)
        assert after["state"] == cl.STATE_WEAKENED
        # La evidencia que lo promovió NO se borra.
        assert after["metrics_at_promotion"] == learning_before["metrics_at_promotion"]
        assert after["source_pattern_ids"] == ["PAT-A", "PAT-B"]
        assert after["learned_at"] is not None
        # Y su historia queda registrada.
        states = [e["to_state"] for e in cl.get_state_events(d.learning_id, db_path=db)]
        assert cl.STATE_WEAKENED in states and cl.STATE_LEARNED in states

    def test_a_contradicting_outcome_contradicts_the_learning(self, db):
        d = _learn(db)
        app = cl.record_application(
            d.learning_id, convergence_id="CONV-001", db_path=db,
        )
        cl.record_outcome(app, outcome_status="loss", db_path=db)
        again = cl.evaluate_convergence(
            _snapshot(evidence_ids=["EV-1", "EV-2", "EV-9"]),
            stats_fetcher=BOTH_STRONG, db_path=db,
        )
        assert again.state == cl.STATE_CONTRADICTED

    def test_a_favourable_outcome_reinforces(self, db):
        d = _learn(db)
        app = cl.record_application(d.learning_id, db_path=db)
        cl.record_outcome(app, outcome_status="win", outcome_value=1.2, db_path=db)
        balance = cl.outcome_balance(d.learning_id, db_path=db)
        assert balance["reinforcing"] == 1
        assert balance["contradicting"] == 0
        assert cl.get_learning(d.learning_id, db_path=db)["state"] == cl.STATE_LEARNED

    def test_reappearance_never_revives_a_contradicted_learning(self, db):
        d = _learn(db)
        assert cl.mark_contradicted(d.learning_id, "prueba", db_path=db)
        revived = cl.evaluate_convergence(
            _snapshot(evidence_ids=["EV-1", "EV-2", "EV-NUEVO"]),
            stats_fetcher=BOTH_STRONG, db_path=db,
        )
        assert revived.state == cl.STATE_CONTRADICTED
        assert cl.get_learning(d.learning_id, db_path=db)["state"] == cl.STATE_CONTRADICTED

    def test_obsolete_is_absorbing_too(self, db):
        d = _learn(db)
        assert cl.mark_obsolete(d.learning_id, "retirado", db_path=db)
        cl.evaluate_convergence(
            _snapshot(evidence_ids=["EV-X"]), stats_fetcher=BOTH_STRONG, db_path=db,
        )
        assert cl.get_learning(d.learning_id, db_path=db)["state"] == cl.STATE_OBSOLETE

    def test_marking_an_unknown_learning_is_refused(self, db):
        assert cl.mark_contradicted("LRN-NO-EXISTE", db_path=db) is False


# ===========================================================================
# Identidad causal reconstruible
# ===========================================================================

class TestCausalIdentity:

    def test_a_learning_persists_every_required_field(self, db):
        d = _learn(db)
        learning = cl.get_learning(d.learning_id, db_path=db)
        for field_name in (
            "learning_id", "claim", "source_convergence_id", "source_pattern_ids",
            "evidence_ids", "policy_id", "policy_version", "metrics_at_promotion",
            "thresholds_at_promotion", "confidence", "learned_at",
            "last_validated_at", "criterion_affected", "criterion_version",
        ):
            assert field_name in learning, f"falta el campo obligatorio {field_name}"
        assert learning["source_convergence_id"] == "CONV-001"
        assert learning["source_pattern_ids"] == ["PAT-A", "PAT-B"]
        assert learning["policy_id"] == "production-market"
        assert learning["criterion_affected"] == "market"
        assert learning["criterion_version"].startswith("crit-")

    def test_the_thresholds_that_promoted_it_are_persisted(self, db):
        d = _learn(db)
        stored = cl.get_learning(d.learning_id, db_path=db)["thresholds_at_promotion"]
        assert stored["min_sample_size"] == 15.0
        assert stored["min_win_rate"] == 55.0
        assert stored["combined_cc"] == 0.4

    def test_the_decision_names_its_evidence_revision(self, db):
        d = _learn(db)
        stored = cl.get_decisions(convergence_id="CONV-001", db_path=db)[0]
        assert stored["evidence_revision_hash"] == d.evidence_revision_hash
        assert len(d.evidence_revision_hash) == 32

    def test_the_revision_covers_the_pattern_evidence(self, db):
        """La evidencia del patrón forma parte de la identidad de la revisión."""
        _, summary = cl.pattern_evidence(["PAT-A", "PAT-B"], _policy(), BOTH_STRONG)
        expected = cl.compute_evidence_revision_hash(
            cl.CausalSnapshot(
                **{**_snapshot().__dict__,
                   "metrics": {**_snapshot().metrics, **summary}},
            )
        )
        assert _learn(db).evidence_revision_hash == expected

    def test_the_chain_is_reconstructible_end_to_end(self, db):
        """convergencia → aprendizaje → criterio → decisión → aplicación → outcome."""
        d = _learn(db)
        learning = cl.get_learning(d.learning_id, db_path=db)

        app_id = cl.record_application(
            d.learning_id, convergence_id="CONV-001",
            criterion_version=learning["criterion_version"], decision_id="DEC-42",
            action_type="rank_domain_evidence", db_path=db,
        )
        out_id = cl.record_outcome(
            app_id, outcome_status="win", outcome_value=1.5, db_path=db,
        )

        app = cl.get_applications(learning_id=d.learning_id, db_path=db)[0]
        assert app["decision_id"] == "DEC-42"
        assert app["criterion_version"] == learning["criterion_version"]
        assert app["outcome_id"] == out_id

        # Desde el outcome se vuelve hasta los patrones de origen sin huecos.
        back = cl.get_learning(app["learning_id"], db_path=db)
        assert back["source_convergence_id"] == app["convergence_id"]
        assert back["source_pattern_ids"] == ["PAT-A", "PAT-B"]

    def test_data_scope_and_test_artifact_flag_are_persisted(self, db):
        cl.register_policy(_policy(), db_path=db)
        d = cl.evaluate_convergence(
            _snapshot(data_scope="market/sim", is_test_artifact=True),
            created_from=cl.CREATED_FROM_TEST, stats_fetcher=BOTH_STRONG, db_path=db,
        )
        learning = cl.get_learning(d.learning_id, db_path=db)
        assert learning["data_scope"] == "market/sim"
        assert learning["is_test_artifact"] is True
        assert learning["created_from"] == cl.CREATED_FROM_TEST

    def test_invalid_created_from_is_rejected(self, db):
        with pytest.raises(ValueError, match="created_from inválido"):
            cl.evaluate_convergence(_snapshot(), created_from="backfill", db_path=db)


# ===========================================================================
# Políticas de producción
# ===========================================================================

class TestProductionPolicies:

    def test_the_four_operational_domains_are_covered(self):
        assert cl.PRODUCTION_DOMAINS == (
            "market", "freight_logistics", "florida_real_estate", "cybersecurity",
        )

    def test_ensure_leaves_no_operational_domain_awaiting_policy(self, db):
        cl.ensure_production_policies(db_path=db)
        for domain in cl.PRODUCTION_DOMAINS:
            policy = cl.get_policy(domain, db_path=db)
            assert policy is not None, f"{domain} quedó sin política"
            d = cl.evaluate_convergence(
                _snapshot(domain=domain), stats_fetcher=BOTH_STRONG, db_path=db,
            )
            assert d.state != cl.STATE_AWAITING_POLICY

    def test_ensure_is_idempotent(self, db):
        assert len(cl.ensure_production_policies(db_path=db)) == 4
        assert cl.ensure_production_policies(db_path=db) == []
        assert len(cl.list_policies(db_path=db)) == 4

    def test_thresholds_come_from_the_real_constants(self):
        from core.domain_knowledge import MIN_EXPECTANCY, MIN_SAMPLE, MIN_WIN_RATE
        from core.learn.gravity_engine import ALERT_MIN_CC, ALERT_MIN_HITS
        p = cl.build_production_policy("market")
        assert p.thresholds["min_sample_size"] == float(MIN_SAMPLE)
        assert p.thresholds["min_win_rate"] == float(MIN_WIN_RATE)
        assert p.thresholds["min_expectancy"] == float(MIN_EXPECTANCY)
        assert p.thresholds["combined_hits"] == float(ALERT_MIN_HITS)
        assert p.thresholds["combined_cc"] == float(ALERT_MIN_CC)

    def test_every_threshold_declares_its_origin(self):
        for domain in cl.PRODUCTION_DOMAINS:
            p = cl.build_production_policy(domain)
            for metric in p.thresholds:
                assert p.threshold_provenance.get(metric), (
                    f"[{domain}] el umbral {metric!r} no declara de dónde sale"
                )

    def test_thresholds_are_identical_across_domains(self):
        """Ningún dominio tiene un umbral rebajado para producir aprendizajes."""
        base = dict(cl.build_production_policy("market").thresholds)
        for domain in cl.PRODUCTION_DOMAINS[1:]:
            assert dict(cl.build_production_policy(domain).thresholds) == base

    def test_required_metric_without_threshold_is_rejected(self):
        with pytest.raises(ValueError, match="sin umbral declarado"):
            cl.LearningPolicy(
                policy_id="p", domain="market", required_metrics=("combined_hits",),
                thresholds={}, threshold_provenance={},
            )

    def test_threshold_without_documented_origin_is_rejected(self):
        with pytest.raises(ValueError, match="sin origen documentado"):
            cl.LearningPolicy(
                policy_id="p", domain="market", required_metrics=("combined_hits",),
                thresholds={"combined_hits": 5.0}, threshold_provenance={},
            )

    def test_a_new_threshold_set_is_a_new_version(self, db):
        cl.register_policy(_policy(policy_version=1), db_path=db)
        cl.register_policy(
            _policy(policy_version=2, thresholds={"combined_cc": 0.9}), db_path=db,
        )
        assert cl.get_policy("market", db_path=db).policy_version == 2
        # La versión anterior NO se borra: se conserva para auditar.
        assert len(cl.list_policies(db_path=db)) == 2


# ===========================================================================
# Cualificación de patrones: el vínculo de identidad
# ===========================================================================

class TestPatternQualification:

    def test_a_strong_pattern_qualifies(self):
        stats = cl.qualify_pattern("PAT-A", _policy(), stats_fetcher=BOTH_STRONG)
        assert stats.qualified is True
        assert stats.sample_size == 20
        assert stats.win_rate == 80.0     # convertido a porcentaje
        assert stats.expectancy == 0.6

    def test_win_rate_is_converted_to_the_percentage_scale(self):
        """`fetch_pattern_stats` da fracción; `MIN_WIN_RATE` es porcentaje."""
        borderline = {"win_rate": 0.56, "expectancy": 0.1, "confidence": 1.0,
                      "sample_size": 20.0}
        stats = cl.qualify_pattern(
            "P", _policy(), stats_fetcher=_fetcher(P=borderline),
        )
        assert stats.win_rate == 56.0
        assert stats.qualified is True, "0.56 debe leerse como 56%, no como 0.56%"

    def test_expectancy_must_be_strictly_positive(self):
        flat = {"win_rate": 0.60, "expectancy": 0.0, "confidence": 1.0,
                "sample_size": 20.0}
        stats = cl.qualify_pattern("P", _policy(), stats_fetcher=_fetcher(P=flat))
        assert stats.qualified is False
        assert "expectancy" in stats.reason

    def test_an_unknown_fingerprint_is_not_assumed_neutral(self):
        stats = cl.qualify_pattern("DESCONOCIDO", _policy(), stats_fetcher=_fetcher())
        assert stats.qualified is False
        assert stats.sample_size == 0
        assert "no tiene outcomes graduados" in stats.reason

    def test_an_empty_fingerprint_is_refused(self):
        stats = cl.qualify_pattern("", _policy(), stats_fetcher=BOTH_STRONG)
        assert stats.qualified is False
        assert "no tiene identificador" in stats.reason

    def test_the_real_provider_accepts_a_gravity_fingerprint(self):
        """El vínculo de identidad existe de verdad, no solo en el doble.

        `fetch_pattern_stats` recibe el MISMO fingerprint que la convergencia
        guarda en `star_a`/`star_b` y devuelve las claves que el gate exige.
        """
        from core.gravity_kernel.signals import fetch_pattern_stats
        import inspect
        assert list(inspect.signature(fetch_pattern_stats).parameters) == ["fingerprint"]
        # Sin registro devuelve None: nunca un valor neutro inventado.
        assert fetch_pattern_stats("fingerprint-que-no-existe-jamas") is None


# ===========================================================================
# Aplicación, abstención y outcome
# ===========================================================================

class TestApplicationAndOutcome:

    def test_record_application_is_idempotent_by_id(self, db):
        d = _learn(db)
        for _ in range(5):
            cl.record_application(
                d.learning_id, application_id="APP-FIXED", db_path=db,
            )
        apps = cl.get_applications(learning_id=d.learning_id, db_path=db)
        assert len(apps) == 1

    def test_an_abstention_is_recorded_too(self, db):
        d = _learn(db)
        cl.record_application(
            d.learning_id, applied=False,
            abstained_reason="governor en pausa", db_path=db,
        )
        app = cl.get_applications(learning_id=d.learning_id, db_path=db)[0]
        assert app["applied"] is False
        assert app["abstained_reason"] == "governor en pausa"

    def test_outcome_for_an_unknown_application_is_refused(self, db):
        assert cl.record_outcome("APP-NO-EXISTE", outcome_status="win", db_path=db) is None
        assert cl.get_applications(db_path=db) == []

    def test_an_outcome_id_cannot_be_reused_across_applications(self, db):
        d = _learn(db)
        a1 = cl.record_application(d.learning_id, db_path=db)
        a2 = cl.record_application(d.learning_id, db_path=db)
        cl.record_outcome(a1, outcome_id="OUT-1", outcome_status="win", db_path=db)
        with pytest.raises(sqlite3.IntegrityError):
            cl.record_outcome(a2, outcome_id="OUT-1", outcome_status="win", db_path=db)


# ===========================================================================
# Ruta, aislamiento y ausencia de efectos al importar
# ===========================================================================

class TestPathResolution:

    def test_vectrax_vault_dir_is_honoured_at_call_time(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "v1"))
        assert cl.default_db_path() == str(tmp_path / "v1" / "causal_learning.db")
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "v2"))
        assert cl.default_db_path() == str(tmp_path / "v2" / "causal_learning.db")

    def test_no_override_falls_back_to_the_project_vault(self, monkeypatch):
        monkeypatch.delenv("VECTRAX_VAULT_DIR", raising=False)
        assert cl.default_db_path() == os.path.join(cl.VAULT_DIR, "causal_learning.db")
        assert cl.default_db_path().endswith("vault/causal_learning.db")

    def test_empty_override_falls_back_to_the_project_vault(self, monkeypatch):
        monkeypatch.setenv("VECTRAX_VAULT_DIR", "")
        assert cl.default_db_path() == os.path.join(cl.VAULT_DIR, "causal_learning.db")

    def test_resolving_the_path_creates_nothing(self, tmp_path, monkeypatch):
        target = tmp_path / "jamas_creado"
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(target))
        cl.default_db_path()
        assert not target.exists()

    def test_the_directory_is_created_only_on_connect(self, db):
        assert not Path(db).parent.exists()
        cl.connect(db).close()
        assert Path(db).is_file()

    def test_the_store_is_not_learned_rules_jsonl(self, tmp_path, monkeypatch):
        """El aprendizaje causal de dominio NO reutiliza el almacén cognitivo."""
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "vault"))
        from core.learn.learned_rules import default_rules_path
        assert cl.default_db_path() != default_rules_path()
        _learn(cl.default_db_path())
        assert not Path(default_rules_path()).exists(), (
            "el puente causal no puede escribir en vault/learned_rules.jsonl"
        )

    def test_wal_mode_is_enabled(self, db):
        conn = cl.connect(db)
        try:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        finally:
            conn.close()

    def test_connect_is_idempotent(self, db):
        for _ in range(3):
            cl.connect(db).close()
        conn = cl.connect(db)
        try:
            names = {r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        assert {
            "learning_policies", "promotion_decisions", "learning_traces",
            "learning_state_events", "learning_applications",
        } <= names


class TestReadersDoNotWrite:

    def test_readers_leave_the_database_byte_identical(self, db):
        import hashlib
        d = _learn(db)
        cl.record_application(d.learning_id, db_path=db)

        def _checkpoint():
            conn = cl.connect(db)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
            return hashlib.sha256(Path(db).read_bytes()).hexdigest()

        before = _checkpoint()
        cl.get_learning(d.learning_id, db_path=db)
        cl.list_learnings(db_path=db)
        cl.consumable_learnings("market", db_path=db)
        cl.get_decisions(convergence_id="CONV-001", db_path=db)
        cl.get_applications(learning_id=d.learning_id, db_path=db)
        cl.get_state_events(d.learning_id, db_path=db)
        cl.count_revisions("CONV-001", db_path=db)
        cl.outcome_balance(d.learning_id, db_path=db)
        cl.list_policies(db_path=db)
        cl.get_policy("market", db_path=db)
        assert _checkpoint() == before

    def test_readers_on_an_empty_store_return_empty(self, db):
        assert cl.list_learnings(db_path=db) == []
        assert cl.consumable_learnings("market", db_path=db) == []
        assert cl.get_decisions(db_path=db) == []
        assert cl.get_applications(db_path=db) == []
        assert cl.get_learning("LRN-NO", db_path=db) is None
        assert cl.count_revisions("CONV-NO", db_path=db) == 0
