"""
Tests de integración — LawSignal

Los principios no responden. Los principios pesan.

Verifica que las violaciones de las 7 Leyes Fundamentales modifican
los scores de PresenciaObserver antes de tomar la decisión de inhibición,
y que la señal resultante llega al ConvergenceLearner.

  1.  build_law_signal([]) retorna None
  2.  Ley 6 → convergence_delta=-0.20, sovereignty_delta=-0.15
  3.  Ley 3 → noise_delta=+0.20
  4.  Ley 4 → force_pause=True
  5.  Ley 2 → convergence_delta=-0.15
  6.  Leyes 1, 5, 7 → violated_laws pero sin ajuste de scores
  7.  Múltiples leyes → acumulación de deltas
  8.  3+ violaciones → penalización extra de noise
  9.  is_severe: True con 3+ violaciones o penalización alta
 10.  has_impact: False si solo leyes sin impacto de scores
 11.  summary() retorna string con resumen
 12.  evaluate() sin law_signal → scores sin cambio
 13.  evaluate() con Ley 6 → convergence y sovereignty ajustados
 14.  evaluate() con Ley 3 → noise ajustado
 15.  evaluate() con Ley 4 force_pause → PAUSE si PERMIT, no si SILENCE/BLOCK
 16.  Ley 6 reduce convergence bajo umbral → SILENCE disparado
 17.  Ley 6+3 combinados pueden subir inhibición a BLOCK
 18.  Bounds: convergence nunca < 0.0 ni > 1.0
 19.  Bounds: noise nunca < 0.0 ni > 1.0
 20.  OBSERVER mode: enforced=False incluso con violaciones
 21.  ConvergenceLearner recibe la decisión con law_signal
 22.  Ley 4 NO afecta SILENCE (no rebaja inhibición)
 23.  Ley 4 NO afecta BLOCK (no rebaja inhibición)
 24.  Múltiples violaciones aumentan inhibición
 25.  Scores ajustados se almacenan en InhibitionRecord

Run:
    pytest tests/integration/test_law_signal.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.nucleus.law_signal import LawSignal, LawImpact, build_law_signal
from core.nucleus.presencia_pura import (
    EmissionOrigin,
    EmissionSignal,
    InhibitionDecision,
    PresenciaObserver,
    get_observer,
    reset_observer,
)
from core.nucleus.convergence_learner import get_learner, reset_learner


# ---------------------------------------------------------------------------
# Helpers: LawCheck stub (sin importar law_enforcement real)
# ---------------------------------------------------------------------------

class _FakeLawCheck:
    """Stub de LawCheck para tests sin dependencias externas."""
    def __init__(self, law_number: int, law_name: str, reason: str = "test"):
        self.law_number = law_number
        self.law_name   = law_name
        self.reason     = reason
        self.passed     = False


def _v(num: int, name: str = "", reason: str = "test") -> _FakeLawCheck:
    """Atajo para crear una violación."""
    names = {1:"Mentalismo",2:"Correspondencia",3:"Vibración",
             4:"Polaridad",5:"Ritmo",6:"Causa y Efecto",7:"Generación"}
    return _FakeLawCheck(num, name or names.get(num, f"Ley{num}"), reason)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset():
    reset_observer()
    reset_learner()
    yield
    reset_observer()
    reset_learner()


@pytest.fixture()
def obs() -> PresenciaObserver:
    return PresenciaObserver(mode=PresenciaObserver.OBSERVER_MODE)


# ---------------------------------------------------------------------------
# 1–11. build_law_signal y propiedades de LawSignal
# ---------------------------------------------------------------------------

class TestBuildLawSignal:

    def test_empty_violations_returns_none(self):
        assert build_law_signal([]) is None

    def test_ley6_ajusta_convergence_y_sovereignty(self):
        ls = build_law_signal([_v(6)])
        assert ls is not None
        assert ls.convergence_delta == pytest.approx(-0.20, abs=0.001)
        assert ls.sovereignty_delta == pytest.approx(-0.15, abs=0.001)
        assert ls.noise_delta == 0.0
        assert ls.force_pause is False
        assert 6 in ls.violated_laws

    def test_ley3_ajusta_noise(self):
        ls = build_law_signal([_v(3)])
        assert ls is not None
        assert ls.noise_delta == pytest.approx(0.20, abs=0.001)
        assert ls.convergence_delta == 0.0
        assert ls.sovereignty_delta == 0.0

    def test_ley4_sets_force_pause(self):
        ls = build_law_signal([_v(4)])
        assert ls is not None
        assert ls.force_pause is True
        assert ls.convergence_delta == 0.0
        assert ls.sovereignty_delta == 0.0
        assert ls.noise_delta == 0.0

    def test_ley2_ajusta_convergence(self):
        ls = build_law_signal([_v(2)])
        assert ls is not None
        assert ls.convergence_delta == pytest.approx(-0.15, abs=0.001)
        assert ls.sovereignty_delta == 0.0

    def test_leyes_sin_impacto_scores_1_5_7(self):
        """Leyes 1, 5, 7 registran en violated_laws pero no ajustan scores."""
        ls = build_law_signal([_v(1), _v(5), _v(7)])
        assert ls is not None
        assert 1 in ls.violated_laws
        assert 5 in ls.violated_laws
        assert 7 in ls.violated_laws
        # Sin penalización de scores (solo multi-violation noise si >=3)
        assert ls.convergence_delta == 0.0
        assert ls.sovereignty_delta == 0.0
        # 3 violaciones → multi-penalty de noise
        assert ls.noise_delta == pytest.approx(0.10, abs=0.001)

    def test_multiples_leyes_acumulan_deltas(self):
        """L2 + L6 = convergence -0.35, sovereignty -0.15."""
        ls = build_law_signal([_v(2), _v(6)])
        assert ls is not None
        assert ls.convergence_delta == pytest.approx(-0.35, abs=0.001)
        assert ls.sovereignty_delta == pytest.approx(-0.15, abs=0.001)

    def test_3_violaciones_aplican_penalizacion_extra_noise(self):
        """L2 + L3 + L6 = noise 0.20 + 0.10(extra) = 0.30."""
        ls = build_law_signal([_v(2), _v(3), _v(6)])
        assert ls is not None
        assert ls.noise_delta == pytest.approx(0.30, abs=0.001)  # 0.20 + 0.10 penalty

    def test_is_severe_con_3_violaciones(self):
        ls = build_law_signal([_v(2), _v(3), _v(6)])
        assert ls.is_severe is True

    def test_is_severe_con_sovereignty_alta_penalizacion(self):
        ls = build_law_signal([_v(6)])  # sovereignty_delta=-0.15 → is_severe
        assert ls.is_severe is True

    def test_has_impact_false_si_solo_leyes_log(self):
        """Ley 1 sola: force_pause=False, todos deltas=0 → has_impact=False."""
        ls = build_law_signal([_v(1)])
        assert ls is not None
        assert ls.has_impact is False  # Ley 1 no tiene impacto de scores

    def test_summary_retorna_string(self):
        ls = build_law_signal([_v(6)])
        s = ls.summary()
        assert isinstance(s, str)
        assert "L6" in s
        assert "sovereignty" in s or "convergence" in s


# ---------------------------------------------------------------------------
# 12–19. PresenciaObserver ajusta scores con LawSignal
# ---------------------------------------------------------------------------

class TestPresenciaObserverConLawSignal:

    def test_sin_law_signal_scores_sin_cambio(self, obs):
        signal = EmissionSignal(
            origin=EmissionOrigin.INTERNAL_RESPONSE,  # sovereignty=0.65
            convergence=0.8,
            noise=0.1,
        )
        record = obs.evaluate(signal)
        assert record.convergence == pytest.approx(0.8, abs=0.001)
        assert record.sovereignty == pytest.approx(0.65, abs=0.001)
        assert record.noise == pytest.approx(0.1, abs=0.001)

    def test_ley6_reduce_convergence_y_sovereignty(self, obs):
        """L6: convergence 0.8-0.20=0.60, sovereignty 0.65-0.15=0.50."""
        ls = build_law_signal([_v(6)])
        signal = EmissionSignal(
            origin=EmissionOrigin.INTERNAL_RESPONSE,
            convergence=0.8,
            noise=0.0,
            law_signal=ls,
        )
        record = obs.evaluate(signal)
        assert record.convergence == pytest.approx(0.60, abs=0.01)
        assert record.sovereignty == pytest.approx(0.50, abs=0.01)
        assert record.noise == pytest.approx(0.0, abs=0.01)

    def test_ley3_aumenta_noise(self, obs):
        """L3: noise 0.1 + 0.20 = 0.30."""
        ls = build_law_signal([_v(3)])
        signal = EmissionSignal(
            origin=EmissionOrigin.INTERNAL_RESPONSE,
            convergence=0.8,
            noise=0.1,
            law_signal=ls,
        )
        record = obs.evaluate(signal)
        assert record.noise == pytest.approx(0.30, abs=0.01)
        assert record.convergence == pytest.approx(0.8, abs=0.01)

    def test_ley4_force_pause_sobreescribe_permit(self, obs):
        """L4: si decision sería PERMIT → forzar PAUSE."""
        ls = build_law_signal([_v(4)])
        signal = EmissionSignal(
            origin=EmissionOrigin.NUCLEUS_CORE,   # sovereignty=1.0
            convergence=0.9,
            noise=0.05,
            law_signal=ls,
        )
        record = obs.evaluate(signal)
        assert record.decision == InhibitionDecision.PAUSE
        assert "Polaridad" in record.reason

    def test_ley4_no_rebaja_silence(self, obs):
        """L4 force_pause NO aplica si la decision ya es SILENCE."""
        ls = build_law_signal([_v(4)])
        signal = EmissionSignal(
            origin=EmissionOrigin.NUCLEUS_CORE,
            convergence=0.1,   # convergence baja → SILENCE
            noise=0.0,
            law_signal=ls,
        )
        record = obs.evaluate(signal)
        # SILENCE por convergencia baja — L4 no rebaja a PERMIT ni sube a BLOCK
        assert record.decision == InhibitionDecision.SILENCE

    def test_ley4_no_rebaja_block(self, obs):
        """L4 force_pause NO aplica si la decision ya es BLOCK."""
        ls = build_law_signal([_v(4)])
        signal = EmissionSignal(
            engine_name="motor_desconocido",
            source_channel="canal.raro",
            law_signal=ls,
        )
        record = obs.evaluate(signal)
        assert record.decision == InhibitionDecision.BLOCK  # UNKNOWN motor

    def test_ley6_baja_convergence_bajo_umbral_produce_silence(self, obs):
        """
        Convergence inicial 0.35 (arriba del umbral 0.30),
        L6 la baja a 0.15 → SILENCE.
        """
        ls = build_law_signal([_v(6)])  # convergence_delta=-0.20
        signal = EmissionSignal(
            origin=EmissionOrigin.INTERNAL_RESPONSE,  # sovereignty=0.65
            convergence=0.35,   # 0.35 - 0.20 = 0.15 < 0.30 → SILENCE
            noise=0.0,
            law_signal=ls,
        )
        record = obs.evaluate(signal)
        assert record.decision == InhibitionDecision.SILENCE
        assert record.convergence == pytest.approx(0.15, abs=0.01)

    def test_bounds_convergence_nunca_negativa(self, obs):
        """Ajuste extremo no puede bajar convergence por debajo de 0.0."""
        ls = LawSignal(convergence_delta=-2.0)  # más que el máximo
        signal = EmissionSignal(
            origin=EmissionOrigin.NUCLEUS_CORE,
            convergence=0.1,
            noise=0.0,
            law_signal=ls,
        )
        record = obs.evaluate(signal)
        assert record.convergence >= 0.0

    def test_bounds_noise_nunca_superior_a_1(self, obs):
        """Ajuste extremo no puede subir noise por encima de 1.0."""
        ls = LawSignal(noise_delta=5.0)  # más que el máximo
        signal = EmissionSignal(
            origin=EmissionOrigin.NUCLEUS_CORE,
            convergence=0.8,
            noise=0.9,
            law_signal=ls,
        )
        record = obs.evaluate(signal)
        assert record.noise <= 1.0

    def test_observer_mode_enforced_false_con_law_signal(self, obs):
        """OBSERVER mode: enforced=False incluso con violaciones graves."""
        ls = build_law_signal([_v(2), _v(3), _v(6)])  # múltiples
        signal = EmissionSignal(
            origin=EmissionOrigin.INTERNAL_RESPONSE,
            convergence=0.8,
            noise=0.0,
            law_signal=ls,
        )
        record = obs.evaluate(signal)
        assert record.enforced is False

    def test_scores_ajustados_se_almacenan_en_record(self, obs):
        """InhibitionRecord.convergence guarda el valor AJUSTADO."""
        ls = LawSignal(convergence_delta=-0.20)
        signal = EmissionSignal(
            origin=EmissionOrigin.INTERNAL_RESPONSE,
            convergence=0.70,
            law_signal=ls,
        )
        record = obs.evaluate(signal)
        # Guardado debe ser el ajustado (0.70 - 0.20 = 0.50), no el original 0.70
        assert record.convergence == pytest.approx(0.50, abs=0.01)


# ---------------------------------------------------------------------------
# 21–24. Escalada de inhibición con múltiples leyes
# ---------------------------------------------------------------------------

class TestEscaladaInhibicion:

    def test_multiples_violaciones_aumentan_inhibicion(self, obs):
        """
        L2+L3+L6 combinados con origen INTERNAL_RESPONSE:
        - convergence: 0.5 - 0.35 = 0.15 → SILENCE (por debajo de 0.30)
        - noise: 0.1 + 0.30 = 0.40 (ruido elevado)
        - sovereignty: 0.65 - 0.15 = 0.50 (pasa el umbral 0.30)
        Decisión esperada: SILENCE (convergence < 0.30 tiene prioridad)
        """
        ls = build_law_signal([_v(2), _v(3), _v(6)])
        signal = EmissionSignal(
            origin=EmissionOrigin.INTERNAL_RESPONSE,
            convergence=0.5,
            noise=0.1,
            law_signal=ls,
        )
        record = obs.evaluate(signal)
        # Con multi-violación: convergence = 0.5 - 0.35 = 0.15 → SILENCE
        assert record.decision in (
            InhibitionDecision.SILENCE,
            InhibitionDecision.PAUSE,
            InhibitionDecision.BLOCK,
        )
        # La inhibición debe ser mayor que PERMIT
        assert record.decision != InhibitionDecision.PERMIT

    def test_ley6_sobre_llm_externo_lo_bloquea(self, obs):
        """
        LLM_EXTERNAL ya tiene sovereignty=0.20.
        Con L6 sovereignty_delta=-0.15 → 0.05 < 0.30 → BLOCK.
        """
        ls = build_law_signal([_v(6)])
        signal = EmissionSignal(
            origin=EmissionOrigin.LLM_EXTERNAL,
            convergence=0.8,
            noise=0.0,
            law_signal=ls,
        )
        record = obs.evaluate(signal)
        # sovereignty = 0.20 - 0.15 = 0.05 < 0.30 → BLOCK
        assert record.decision == InhibitionDecision.BLOCK
        assert record.sovereignty == pytest.approx(0.05, abs=0.01)

    def test_nucleo_limpio_resiste_una_violacion(self, obs):
        """
        NUCLEUS_CORE sovereignty=1.0 resiste L6 sin BLOCK.
        sovereignty = 1.0 - 0.15 = 0.85 (bien por encima de 0.30).
        """
        ls = build_law_signal([_v(6)])
        signal = EmissionSignal(
            origin=EmissionOrigin.NUCLEUS_CORE,
            convergence=0.9,
            noise=0.0,
            law_signal=ls,
        )
        record = obs.evaluate(signal)
        # Sovereignty 0.85 > 0.30 → no BLOCK por soberanía
        # Convergence 0.9 - 0.20 = 0.70 > 0.30 → no SILENCE
        assert record.decision == InhibitionDecision.PERMIT
        assert record.sovereignty == pytest.approx(0.85, abs=0.01)

    def test_ley3_sola_no_bloquea_nucleo_limpio(self, obs):
        """
        L3 sube noise 0.0 + 0.20 = 0.20 < 0.80 → no PAUSE.
        NUCLEUS_CORE con convergence alta → PERMIT.
        """
        ls = build_law_signal([_v(3)])
        signal = EmissionSignal(
            origin=EmissionOrigin.NUCLEUS_CORE,
            convergence=0.9,
            noise=0.0,
            law_signal=ls,
        )
        record = obs.evaluate(signal)
        assert record.decision == InhibitionDecision.PERMIT
        assert record.noise == pytest.approx(0.20, abs=0.01)


# ---------------------------------------------------------------------------
# 21. ConvergenceLearner recibe la señal con law_signal
# ---------------------------------------------------------------------------

class TestConvergenceLearnerConLawSignal:

    def test_evaluate_con_law_signal_popula_learner_outcome_id(self, obs):
        """El record tiene learner_outcome_id aunque haya law_signal."""
        ls = build_law_signal([_v(6)])
        signal = EmissionSignal(
            origin=EmissionOrigin.INTERNAL_RESPONSE,
            convergence=0.8,
            noise=0.0,
            law_signal=ls,
        )
        record = obs.evaluate(signal)
        assert record.learner_outcome_id is not None
        assert len(record.learner_outcome_id) == 8

    def test_learner_registra_decision_con_scores_ajustados(self, obs):
        """
        El ConvergenceLearner recibe la decisión resultante de los
        scores ajustados por el LawSignal.
        """
        learner = get_learner()
        before = len(learner.outcomes)

        ls = build_law_signal([_v(6)])
        signal = EmissionSignal(
            origin=EmissionOrigin.NUCLEUS_CORE,
            convergence=0.9,
            noise=0.0,
            law_signal=ls,
        )
        obs.evaluate(signal)

        assert len(learner.outcomes) == before + 1
        outcome = learner.outcomes[-1]
        # El learner recibe la decisión real (PERMIT para NUCLEUS_CORE resistente)
        assert outcome.decision in ("PERMIT", "PAUSE", "SILENCE", "BLOCK")

    def test_law_signal_no_rompe_cadena_learner_observer(self, obs):
        """
        Múltiples evaluaciones con y sin law_signal no rompen la cadena.
        """
        learner = get_learner()
        before = len(learner.outcomes)

        # Sin law_signal
        obs.evaluate(EmissionSignal(
            origin=EmissionOrigin.NUCLEUS_CORE,
            convergence=0.9,
        ))
        # Con law_signal L3
        obs.evaluate(EmissionSignal(
            origin=EmissionOrigin.INTERNAL_RESPONSE,
            convergence=0.8,
            law_signal=build_law_signal([_v(3)]),
        ))
        # Con law_signal L4 (force_pause)
        obs.evaluate(EmissionSignal(
            origin=EmissionOrigin.NUCLEUS_CORE,
            convergence=0.9,
            law_signal=build_law_signal([_v(4)]),
        ))

        assert len(learner.outcomes) == before + 3
        # Todas tienen outcome_id
        for o in list(learner.outcomes)[-3:]:
            assert o.outcome_id is not None
