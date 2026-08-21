"""
tests/test_decision_authority.py — cobertura del fix de nombre de acción
"respond" (ver plan "Conectar el veredicto constitucional (Kybalion) a
bloqueo real en external_gateway.py").

Antes del fix, `check_authority("respond")` caía al branch "Desconocida"
(auto_approved=False SIEMPRE), porque `AUTO_ACTIONS` solo tenía
"generate_response". `external_gateway.py` construye
`ActionProposal(action="respond", ...)` — sin este fix, conectar
DecisionAuthority real habría bloqueado el 100% de las respuestas.
"""
from __future__ import annotations

from core.operator.decision_authority import (
    Authority,
    check_authority,
    can_auto_execute,
    requires_creator,
)


class TestRespondActionIsAuto:
    def test_respond_is_auto_approved(self):
        decision = check_authority("respond")
        assert decision.authority == Authority.AUTO
        assert decision.auto_approved is True

    def test_respond_can_auto_execute_shortcut(self):
        assert can_auto_execute("respond") is True

    def test_respond_does_not_require_creator(self):
        assert requires_creator("respond") is False

    def test_generate_response_still_auto_approved(self):
        """El nombre histórico ('generate_response') sigue funcionando —
        el fix es aditivo, no reemplaza nada."""
        decision = check_authority("generate_response")
        assert decision.authority == Authority.AUTO
        assert decision.auto_approved is True

    def test_respond_paused_during_recover_mode_is_not_in_pause_set(self):
        """'respond' NO está en el set especial que se pausa durante
        governor_mode='recover' (solo learning_cycle/gravity_rebalance/
        detect_patterns lo están) — sigue auto-aprobándose incluso en
        recuperación, ya que responder al usuario no es una acción de
        mantenimiento interno."""
        decision = check_authority("respond", governor_mode="recover")
        assert decision.auto_approved is True
