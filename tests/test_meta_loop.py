"""
Tests — core/meta_loop.py
==========================
Cubre:
  - reflect() básico (activity, health, uptime)
  - Layer 4: auto-refresh IdeaStore cada 15 min
  - Alertas proactivas al creador para ideas HIGH/CRITICAL
  - Límite de alertas por ciclo (MAX_ALERTS_PER_CYCLE)
  - No re-enviar ideas ya alertadas
  - Silencio cuando TELEGRAM_CREATOR_CHAT_ID no está configurado
"""

import os
import sys
import time
import importlib
from unittest.mock import patch, MagicMock, call
import pytest

# Asegurar que el root del proyecto está en path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reload_meta_loop():
    """Recarga meta_loop limpiando su estado global entre tests."""
    import core.meta_loop as ml
    importlib.reload(ml)
    return ml


def _make_fake_idea(idea_id, priority_value, priority_score=0.7):
    """Genera un objeto Idea fake con los campos mínimos necesarios."""
    idea = MagicMock()
    idea.idea_id = idea_id
    idea.title = f"Idea {idea_id}"
    idea.affected_component = "test_component"
    idea.priority_score = priority_score

    # Simular IdeaPriority enum
    prio = MagicMock()
    prio.value = priority_value
    idea.priority = prio
    return idea


# ---------------------------------------------------------------------------
# Fixture: estado base mínimo
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_state_manager():
    """Evita que los tests lean/escriban el estado real del sistema."""
    fake_state = {
        "errors_since_boot": 0,
        "cycles": 5,
        "boot_time": "2026-05-22T10:00:00",
    }
    with patch("core.meta_loop.state_manager") as mock_sm:
        mock_sm.load.return_value = fake_state.copy()
        mock_sm.save.return_value = None
        yield mock_sm


# ---------------------------------------------------------------------------
# Tests básicos de reflect()
# ---------------------------------------------------------------------------

class TestReflectBasic:
    def test_returns_dict_with_required_keys(self):
        ml = _reload_meta_loop()
        result = ml.reflect(ingested_count=0)
        assert "timestamp" in result
        assert "activity" in result
        assert "health" in result
        assert "cycles" in result
        assert "uptime" in result

    def test_activity_idle_when_no_ingests(self):
        ml = _reload_meta_loop()
        result = ml.reflect(ingested_count=0)
        assert result["activity"] == "idle"

    def test_activity_active_when_ingested(self):
        ml = _reload_meta_loop()
        result = ml.reflect(ingested_count=3)
        assert "active" in result["activity"]
        assert "3" in result["activity"]

    def test_health_nominal_no_errors(self):
        ml = _reload_meta_loop()
        result = ml.reflect()
        assert result["health"] == "nominal"

    def test_health_degraded_few_errors(self):
        import core.meta_loop as ml
        with patch("core.meta_loop.state_manager") as mock_sm:
            mock_sm.load.return_value = {
                "errors_since_boot": 3,
                "cycles": 10,
                "boot_time": None,
            }
            mock_sm.save.return_value = None
            result = ml.reflect()
        assert "degraded" in result["health"]

    def test_health_critical_many_errors(self):
        import core.meta_loop as ml
        with patch("core.meta_loop.state_manager") as mock_sm:
            mock_sm.load.return_value = {
                "errors_since_boot": 8,
                "cycles": 20,
                "boot_time": None,
            }
            mock_sm.save.return_value = None
            result = ml.reflect()
        assert "critical" in result["health"]

    def test_state_is_saved_after_reflect(self):
        import core.meta_loop as ml
        with patch("core.meta_loop.state_manager") as mock_sm:
            mock_sm.load.return_value = {
                "errors_since_boot": 0,
                "cycles": 5,
                "boot_time": None,
            }
            mock_sm.save.return_value = None
            ml.reflect()
        mock_sm.save.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — Layer 4: IdeaStore auto-refresh
# ---------------------------------------------------------------------------

class TestIdeaRefreshTrigger:
    def test_no_refresh_on_first_cycle_if_interval_not_elapsed(self):
        """Si el intervalo no ha pasado, no debe llamar a store.refresh()."""
        ml = _reload_meta_loop()
        # Simular que el último refresh fue hace 5 minutos (< 15 min)
        ml._last_idea_refresh = time.time() - 300

        with patch("core.idea_store.get_idea_store") as mock_store:
            ml.reflect()
            mock_store.return_value.refresh.assert_not_called()

    def test_refresh_triggered_when_interval_elapsed(self):
        """Después de 15 min, debe llamar a store.refresh()."""
        ml = _reload_meta_loop()
        # Simular que el último refresh fue hace 20 minutos
        ml._last_idea_refresh = time.time() - 1200

        mock_store = MagicMock()
        mock_store.refresh.return_value = {"router_proposals": 0, "router_analysis": 0, "convergence": 0}
        mock_store.pending.return_value = []

        with patch("core.idea_store.get_idea_store", return_value=mock_store):
            with patch.dict(os.environ, {"TELEGRAM_CREATOR_CHAT_ID": ""}):
                ml.reflect()
        mock_store.refresh.assert_called_once()

    def test_last_refresh_time_updated_after_trigger(self):
        ml = _reload_meta_loop()
        ml._last_idea_refresh = 0.0  # force trigger

        mock_store = MagicMock()
        mock_store.refresh.return_value = {}
        mock_store.pending.return_value = []

        with patch("core.idea_store.get_idea_store", return_value=mock_store):
            with patch.dict(os.environ, {"TELEGRAM_CREATOR_CHAT_ID": ""}):
                before = time.time()
                ml.reflect()
                assert ml._last_idea_refresh >= before

    def test_idea_alerts_sent_included_in_reflection(self):
        """El resultado de reflect() debe incluir idea_alerts_sent cuando se refresca."""
        ml = _reload_meta_loop()
        ml._last_idea_refresh = 0.0

        mock_store = MagicMock()
        mock_store.refresh.return_value = {}
        mock_store.pending.return_value = []

        with patch("core.idea_store.get_idea_store", return_value=mock_store):
            with patch.dict(os.environ, {"TELEGRAM_CREATOR_CHAT_ID": ""}):
                result = ml.reflect()
        assert "idea_alerts_sent" in result

    def test_idea_alerts_not_in_reflection_when_no_refresh(self):
        """Cuando no hay refresh, idea_alerts_sent NO debe estar en el resultado."""
        ml = _reload_meta_loop()
        ml._last_idea_refresh = time.time()  # ya refrescado ahora mismo

        result = ml.reflect()
        assert "idea_alerts_sent" not in result


# ---------------------------------------------------------------------------
# Tests — alertas proactivas al creador
# ---------------------------------------------------------------------------

class TestCreatorAlerts:
    def _setup_store_with_ideas(self, ideas):
        mock_store = MagicMock()
        mock_store.refresh.return_value = {}
        mock_store.pending.return_value = ideas
        return mock_store

    def test_no_alert_without_creator_chat_id(self):
        """Sin TELEGRAM_CREATOR_CHAT_ID configurado, no debe enviar nada."""
        ml = _reload_meta_loop()
        ml._last_idea_refresh = 0.0

        idea = _make_fake_idea("IDEA-A0000001", "high", 0.8)
        mock_store = self._setup_store_with_ideas([idea])

        with patch("core.idea_store.get_idea_store", return_value=mock_store):
            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("TELEGRAM_CREATOR_CHAT_ID", None)
                with patch("core.meta_loop._send_telegram") as mock_send:
                    ml.reflect()
                    mock_send.assert_not_called()

    def test_alert_sent_for_high_priority_idea(self):
        """Una idea HIGH con creator chat configurado debe generar alerta."""
        ml = _reload_meta_loop()
        ml._last_idea_refresh = 0.0

        from core.idea_store import IdeaPriority, IdeaSource, Idea
        import datetime as dt

        idea = Idea(
            idea_id="IDEA-B0000002",
            title="Idea HIGH test",
            description="desc",
            source=IdeaSource.MANUAL,
            priority=IdeaPriority.HIGH,
            impact_score=0.75,
            convergence_level=0.6,
            affected_component="test_component",
            evidence={},
            created_at=dt.datetime.now().isoformat(),
        )
        mock_store = self._setup_store_with_ideas([idea])

        with patch("core.idea_store.get_idea_store", return_value=mock_store):
            with patch.dict(os.environ, {"TELEGRAM_CREATOR_CHAT_ID": "12345"}):
                with patch("core.meta_loop._send_telegram", return_value=True) as mock_send:
                    count = ml._run_idea_refresh()
        assert count == 1
        mock_send.assert_called_once()

    def test_alert_sent_for_critical_priority_idea(self):
        """Una idea CRITICAL debe generar alerta."""
        ml = _reload_meta_loop()
        ml._last_idea_refresh = 0.0

        from core.idea_store import IdeaPriority, IdeaStatus, Idea
        import uuid, datetime as dt

        # Crear idea real usando el dataclass
        idea = Idea(
            idea_id="IDEA-CC000001",
            title="Test critical",
            description="desc",
            source=__import__("core.idea_store", fromlist=["IdeaSource"]).IdeaSource.MANUAL,
            priority=IdeaPriority.CRITICAL,
            impact_score=0.95,
            convergence_level=0.8,
            affected_component="router",
            evidence={},
            created_at=dt.datetime.now().isoformat(),
        )

        mock_store = MagicMock()
        mock_store.refresh.return_value = {}
        mock_store.pending.return_value = [idea]

        with patch("core.idea_store.get_idea_store", return_value=mock_store):
            with patch.dict(os.environ, {"TELEGRAM_CREATOR_CHAT_ID": "99999"}):
                with patch("core.meta_loop._send_telegram", return_value=True) as mock_send:
                    count = ml._run_idea_refresh()
        assert count == 1
        mock_send.assert_called_once()
        # El mensaje debe contener el idea_id
        call_args = mock_send.call_args
        assert "IDEA-CC000001" in call_args[0][1]

    def test_max_alerts_per_cycle_respected(self):
        """No debe enviar más de MAX_ALERTS_PER_CYCLE alertas por ciclo."""
        ml = _reload_meta_loop()
        ml._last_idea_refresh = 0.0

        from core.idea_store import IdeaPriority, IdeaStatus, Idea
        import datetime as dt

        source = __import__("core.idea_store", fromlist=["IdeaSource"]).IdeaSource.MANUAL

        # Crear 5 ideas CRITICAL
        ideas = []
        for i in range(5):
            idea = Idea(
                idea_id=f"IDEA-MAX{i:05d}",
                title=f"Idea crítica {i}",
                description="desc",
                source=source,
                priority=IdeaPriority.CRITICAL,
                impact_score=0.9,
                convergence_level=0.7,
                affected_component="test",
                evidence={},
                created_at=dt.datetime.now().isoformat(),
            )
            ideas.append(idea)

        mock_store = MagicMock()
        mock_store.refresh.return_value = {}
        mock_store.pending.return_value = ideas

        with patch("core.idea_store.get_idea_store", return_value=mock_store):
            with patch.dict(os.environ, {"TELEGRAM_CREATOR_CHAT_ID": "12345"}):
                with patch("core.meta_loop._send_telegram", return_value=True) as mock_send:
                    count = ml._run_idea_refresh()

        # Máximo 3 alertas aunque haya 5 ideas
        assert mock_send.call_count <= ml._MAX_ALERTS_PER_CYCLE
        assert count <= ml._MAX_ALERTS_PER_CYCLE

    def test_no_duplicate_alerts_for_same_idea(self):
        """Una idea ya alertada no debe recibir segunda alerta en el siguiente ciclo."""
        ml = _reload_meta_loop()

        from core.idea_store import IdeaPriority, Idea
        import datetime as dt

        source = __import__("core.idea_store", fromlist=["IdeaSource"]).IdeaSource.MANUAL

        idea = Idea(
            idea_id="IDEA-DUP00001",
            title="Idea duplicada test",
            description="desc",
            source=source,
            priority=IdeaPriority.HIGH,
            impact_score=0.8,
            convergence_level=0.6,
            affected_component="test",
            evidence={},
            created_at=dt.datetime.now().isoformat(),
        )

        mock_store = MagicMock()
        mock_store.refresh.return_value = {}
        mock_store.pending.return_value = [idea]

        with patch("core.idea_store.get_idea_store", return_value=mock_store):
            with patch.dict(os.environ, {"TELEGRAM_CREATOR_CHAT_ID": "12345"}):
                with patch("core.meta_loop._send_telegram", return_value=True) as mock_send:
                    # Primer ciclo — debe alertar
                    ml._last_idea_refresh = 0.0
                    count1 = ml._run_idea_refresh()

                    # Segundo ciclo — misma idea, no debe re-alertar
                    ml._last_idea_refresh = 0.0
                    count2 = ml._run_idea_refresh()

        assert count1 == 1
        assert count2 == 0
        assert mock_send.call_count == 1


# ---------------------------------------------------------------------------
# Tests — _send_telegram
# ---------------------------------------------------------------------------

class TestSendTelegram:
    def test_returns_false_without_token(self):
        ml = _reload_meta_loop()
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            result = ml._send_telegram("12345", "hola")
        assert result is False

    def test_returns_false_without_chat_id(self):
        ml = _reload_meta_loop()
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "fake_token"}):
            result = ml._send_telegram("", "hola")
        assert result is False

    def test_returns_true_on_200_response(self):
        ml = _reload_meta_loop()
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "fake:token"}):
            with patch("core.telegram_guard.should_send_to_user", return_value=True):
                with patch("httpx.post", return_value=mock_resp) as mock_post:
                    result = ml._send_telegram("999", "test msg")

        assert result is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "sendMessage" in call_kwargs[0][0]

    def test_returns_false_on_non_200(self):
        ml = _reload_meta_loop()
        mock_resp = MagicMock()
        mock_resp.status_code = 400

        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "fake:token"}):
            with patch("core.telegram_guard.should_send_to_user", return_value=True):
                with patch("httpx.post", return_value=mock_resp):
                    result = ml._send_telegram("999", "msg")
        assert result is False

    def test_returns_false_on_exception(self):
        ml = _reload_meta_loop()
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "fake:token"}):
            with patch("core.telegram_guard.should_send_to_user", return_value=True):
                with patch("httpx.post", side_effect=Exception("network error")):
                    result = ml._send_telegram("999", "msg")
        assert result is False


# ---------------------------------------------------------------------------
# Tests — _compute_uptime
# ---------------------------------------------------------------------------

class TestComputeUptime:
    def test_unknown_when_none(self):
        ml = _reload_meta_loop()
        assert ml._compute_uptime(None) == "unknown"

    def test_minutes_only(self):
        ml = _reload_meta_loop()
        from datetime import datetime, timedelta
        boot = (datetime.now() - timedelta(minutes=30)).isoformat()
        result = ml._compute_uptime(boot)
        assert "m" in result
        assert "30" in result

    def test_hours_and_minutes(self):
        ml = _reload_meta_loop()
        from datetime import datetime, timedelta
        boot = (datetime.now() - timedelta(hours=2, minutes=15)).isoformat()
        result = ml._compute_uptime(boot)
        assert "2h" in result
        assert "15m" in result

    def test_invalid_string_returns_unknown(self):
        ml = _reload_meta_loop()
        assert ml._compute_uptime("not-a-date") == "unknown"
