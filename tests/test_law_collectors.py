"""
Tests — Colectores de señal real para las 7 Leyes Fundamentales.

Verifican que las leyes 1, 3, 5 (y el hook de la 7) se conectan al estado
REAL del sistema sin romper el contrato de enforce_all_laws (defaults intactos,
fail-safe cuando el estado es indeterminado).

Run:
    pytest tests/test_law_collectors.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.operator.law_enforcement import (
    detect_active_cycles,
    detect_governor_mode,
    detect_forcing_during_recovery,
    detect_knowledge_verified,
    enforce_all_laws,
)


# ---------------------------------------------------------------------------
# Colectores — no lanzan, tipos correctos, fail-safe
# ---------------------------------------------------------------------------

class TestCollectorsAreSafe:
    def test_detect_active_cycles_returns_bool(self):
        assert isinstance(detect_active_cycles(), bool)

    def test_detect_governor_mode_returns_str(self):
        m = detect_governor_mode()
        assert isinstance(m, str) and m

    def test_detect_knowledge_verified_returns_bool(self):
        assert isinstance(detect_knowledge_verified(), bool)


# ---------------------------------------------------------------------------
# Ley 5 — forcing durante recovery (señal real del Governor)
# ---------------------------------------------------------------------------

class TestForcingDuringRecovery:
    def test_recover_modes_are_forcing(self):
        assert detect_forcing_during_recovery("recover") is True
        assert detect_forcing_during_recovery("recovery") is True
        assert detect_forcing_during_recovery("RECOVERING") is True

    def test_normal_modes_not_forcing(self):
        assert detect_forcing_during_recovery("observe") is False
        assert detect_forcing_during_recovery("act") is False
        assert detect_forcing_during_recovery("") is False


# ---------------------------------------------------------------------------
# enforce_all_laws — wiring de Ley 3 (Vibración) + backward compat
# ---------------------------------------------------------------------------

class TestEnforceLaw3Wiring:
    def _all_good(self, **over):
        base = dict(
            input_classified=True,
            classification="market",
            interaction_recorded=True,
            action_logged=True,
            has_correlation_id=True,
        )
        base.update(over)
        return enforce_all_laws(**base)

    def test_default_active_cycles_passes(self):
        """Default system_has_active_cycles=True → Ley 3 pasa (backward compat)."""
        result = self._all_good()
        assert result.all_passed is True
        assert len(result.checks) == 7

    def test_stagnation_triggers_law3_violation(self):
        """system_has_active_cycles=False → Ley 3 viola (estancamiento)."""
        result = self._all_good(system_has_active_cycles=False)
        assert result.all_passed is False
        l3 = [v for v in result.violations if v.law_number == 3]
        assert len(l3) == 1
        assert l3[0].law_name == "Vibración"

    def test_forcing_during_recovery_triggers_law5(self):
        """forcing_during_recovery=True → Ley 5 (Ritmo) viola."""
        result = self._all_good(
            governor_mode="recover", forcing_during_recovery=True,
        )
        assert result.all_passed is False
        l5 = [v for v in result.violations if v.law_number == 5]
        assert len(l5) == 1
