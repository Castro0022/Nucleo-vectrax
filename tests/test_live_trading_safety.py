"""
tests/test_live_trading_safety.py — Corrección de seguridad LIVE, 2026-09-26.

Contexto verificado antes de esta corrección: `record_trade_result(is_paper=False)`
nunca se llamaba en producción (nada abría el registro de una posición LIVE, así
que nada podía después reportar su cierre), por lo que `max_daily_loss_usd` y
`max_consecutive_losses` nunca se activaban en modo LIVE. `max_positions_open`
existía solo en texto de estado, sin ningún chequeo real. `/vx etoro auto config`
aceptaba cualquier valor numérico sin rango.

Cubre exactamente lo pedido:
  - cierre LIVE registrado una sola vez
  - cierre duplicado ignorado
  - max_positions_open bloquea (PAPER local, LIVE contra el bróker)
  - fallo del bróker bloquea en LIVE (fail closed)
  - PnL no disponible no se registra (closed_pnl_pending, sin
    record_trade_result, con aviso por Telegram)
  - valores inválidos en Telegram rechazados (validate_risk_limit)

Cambio adicional en el mismo PR (2026-09-26, sin merge/deploy todavía):
los cortacircuitos YA NO revierten el modo a PAPER. En vez de eso:
  - 3 pérdidas LIVE seguidas → pausa de 24h (el modo se queda en LIVE)
  - límite de pérdida diaria → pausa de 24h (el modo se queda en LIVE)
  - check_risk_before_trade() bloquea toda entrada nueva durante la pausa
  - pasadas las 24h, la pausa se levanta sola (sin intervención manual) y
    reinicia daily_loss_usd/consecutive_losses
  - avisa por Telegram al pausar (motivo + hora de reanudación) y al
    reanudar
  - las posiciones ya abiertas nunca se tocan durante la pausa
  - el HALT manual es independiente: solo se levanta con el comando del
    creador; la pausa automática nunca lo toca
  - la pausa vive en el mismo JSON persistido que el resto de la config,
    así que un reinicio de procesos no la borra ni la acorta

Nunca coloca una orden real: `trade_executor.execute_open` está
envenenado (poison pill) en todos los tests de este archivo.
"""
from __future__ import annotations

import itertools
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from connectors.etoro import auto_executor
from connectors.etoro.auto_executor import AutoMode


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture()
def isolated_executor(tmp_path, monkeypatch):
    """Apunta config + logs PAPER/LIVE del auto-executor a archivos temporales
    frescos — mismo patrón que `isolated_executor` en
    test_etoro_auto_live_promotion.py, extendido con _LIVE_LOG_FILE."""
    monkeypatch.setattr(auto_executor, "_CONFIG_FILE", str(tmp_path / "auto_cfg.json"))
    monkeypatch.setattr(auto_executor, "_PAPER_LOG_FILE", str(tmp_path / "paper_trades.jsonl"))
    monkeypatch.setattr(auto_executor, "_LIVE_LOG_FILE", str(tmp_path / "live_trades.jsonl"))
    return tmp_path


@pytest.fixture(autouse=True)
def no_real_orders(monkeypatch):
    """Poison pill: cualquier intento de colocar una orden real revienta el
    test — ningún test de este archivo debe llegar a trade_executor.execute_open."""
    def _boom(*a, **k):
        raise AssertionError(
            "un test de seguridad LIVE nunca debe colocar una orden real"
        )
    from connectors.etoro import trade_executor
    monkeypatch.setattr(trade_executor, "execute_open", _boom)


@pytest.fixture(autouse=True)
def tg_calls(monkeypatch):
    """Captura las notificaciones de Telegram en vez de tocar la red."""
    from connectors.etoro import learning_engine
    sent = []
    monkeypatch.setattr(
        learning_engine, "_tg_notify", lambda text: sent.append(text) or True
    )
    return sent


def _live_mode_cfg(**overrides):
    cfg = dict(auto_executor.DEFAULTS)
    cfg.update({
        "mode": AutoMode.LIVE.value,
        "live_activated_at": 1.0,
        "live_activated_by": "test",
    })
    cfg.update(overrides)
    return cfg


_trade_seq = itertools.count(1)


def _new_position_id() -> str:
    return f"POS-TEST-{next(_trade_seq)}"


# ── 1 y 2: registro único / duplicado ignorado ─────────────────────────────

class TestLiveCloseRecordedOnce:
    def test_live_close_registered_once(self, isolated_executor):
        auto_executor._save_config(_live_mode_cfg())
        pid = _new_position_id()
        auto_executor.record_trade_result(-1.0, is_paper=False, trade_id=f"LIVE-{pid}")

        cfg = auto_executor.get_config()
        assert cfg["consecutive_losses"] == 1
        assert cfg["daily_loss_usd"] == pytest.approx(1.0)
        assert f"LIVE-{pid}" in cfg["recorded_live_trade_ids"]

    def test_duplicate_live_close_ignored(self, isolated_executor):
        auto_executor._save_config(_live_mode_cfg())
        pid = _new_position_id()
        tid = f"LIVE-{pid}"

        auto_executor.record_trade_result(-1.0, is_paper=False, trade_id=tid)
        cfg_after_first = auto_executor.get_config()
        assert cfg_after_first["consecutive_losses"] == 1
        assert cfg_after_first["daily_loss_usd"] == pytest.approx(1.0)

        # Reenvío del MISMO trade_id (p. ej. el chequeo de 5 min y el de
        # 30 min solapándose sobre la misma posición) -- no debe sumar
        # una segunda vez.
        auto_executor.record_trade_result(-1.0, is_paper=False, trade_id=tid)
        cfg_after_dup = auto_executor.get_config()
        assert cfg_after_dup["consecutive_losses"] == 1
        assert cfg_after_dup["daily_loss_usd"] == pytest.approx(1.0)

    def test_trade_id_required_for_live_too(self, isolated_executor):
        auto_executor._save_config(_live_mode_cfg())
        with pytest.raises(ValueError):
            auto_executor.record_trade_result(-1.0, is_paper=False, trade_id=None)

    def test_trade_id_required_for_paper_unchanged(self, isolated_executor):
        """No regresión: la exigencia de trade_id en PAPER sigue igual."""
        auto_executor._save_config(dict(auto_executor.DEFAULTS))
        with pytest.raises(ValueError):
            auto_executor.record_trade_result(-1.0, is_paper=True, trade_id=None)


# ── 3: 3 pérdidas LIVE seguidas → PAPER ─────────────────────────────────────

class TestLiveConsecutiveLossesPause:
    """Corrección adicional 2026-09-26: el cortacircuito YA NO revierte el
    modo a PAPER -- activa una pausa de 24h y el modo se queda en LIVE
    todo el tiempo."""

    def test_three_live_losses_activate_pause_mode_stays_live(self, isolated_executor, tg_calls):
        auto_executor._save_config(_live_mode_cfg(max_consecutive_losses=3))

        for _ in range(3):
            auto_executor.record_trade_result(
                -1.0, is_paper=False, trade_id=f"LIVE-{_new_position_id()}"
            )

        cfg = auto_executor.get_config()
        assert cfg["mode"] == AutoMode.LIVE.value          # el modo NUNCA cambia
        assert cfg["consecutive_losses"] == 3
        assert cfg["paused_until"] > time.time()
        assert "pérdidas consecutivas" in cfg["pause_reason"]
        assert any("pausad" in msg.lower() for msg in tg_calls)  # avisó por Telegram

    def test_two_live_losses_do_not_pause(self, isolated_executor):
        auto_executor._save_config(_live_mode_cfg(max_consecutive_losses=3))
        for _ in range(2):
            auto_executor.record_trade_result(
                -1.0, is_paper=False, trade_id=f"LIVE-{_new_position_id()}"
            )
        cfg = auto_executor.get_config()
        assert cfg["mode"] == AutoMode.LIVE.value
        assert cfg["paused_until"] == 0

    def test_a_win_resets_consecutive_losses(self, isolated_executor):
        auto_executor._save_config(_live_mode_cfg(max_consecutive_losses=3))
        auto_executor.record_trade_result(-1.0, is_paper=False, trade_id=f"LIVE-{_new_position_id()}")
        auto_executor.record_trade_result(-1.0, is_paper=False, trade_id=f"LIVE-{_new_position_id()}")
        auto_executor.record_trade_result(5.0, is_paper=False, trade_id=f"LIVE-{_new_position_id()}")
        cfg = auto_executor.get_config()
        assert cfg["consecutive_losses"] == 0
        assert cfg["mode"] == AutoMode.LIVE.value
        assert cfg["paused_until"] == 0

    def test_second_breaker_does_not_extend_an_active_pause(self, isolated_executor):
        """Si pérdidas consecutivas Y pérdida diaria disparan en el mismo
        cierre, la pausa no se extiende ni el motivo se pisa dos veces."""
        cfg0 = _live_mode_cfg(max_consecutive_losses=1, max_daily_loss_usd=0.5)
        auto_executor._save_config(cfg0)
        auto_executor.record_trade_result(-5.0, is_paper=False, trade_id=f"LIVE-{_new_position_id()}")
        cfg = auto_executor.get_config()
        first_paused_until = cfg["paused_until"]
        first_reason = cfg["pause_reason"]
        assert first_paused_until > 0

        # Otro cierre perdedor con la pausa ya activa: check_risk_before_trade
        # bloquearía la entrada antes de llegar aquí en el camino real, pero
        # record_trade_result() en sí no debe tocar una pausa ya vigente.
        auto_executor.record_trade_result(-5.0, is_paper=False, trade_id=f"LIVE-{_new_position_id()}")
        cfg2 = auto_executor.get_config()
        assert cfg2["paused_until"] == first_paused_until
        assert cfg2["pause_reason"] == first_reason


# ── 4: límite de pérdida diaria → pausa 24h (modo se queda en LIVE) ────────

class TestLiveDailyLossPause:
    def test_daily_loss_limit_activates_pause_mode_stays_live(self, isolated_executor, tg_calls):
        auto_executor._save_config(_live_mode_cfg(max_daily_loss_usd=10.0, max_consecutive_losses=99))
        auto_executor.record_trade_result(-6.0, is_paper=False, trade_id=f"LIVE-{_new_position_id()}")
        cfg = auto_executor.get_config()
        assert cfg["mode"] == AutoMode.LIVE.value
        assert cfg["paused_until"] == 0  # todavía no llega al límite

        auto_executor.record_trade_result(-6.0, is_paper=False, trade_id=f"LIVE-{_new_position_id()}")
        cfg = auto_executor.get_config()
        assert cfg["mode"] == AutoMode.LIVE.value           # el modo NUNCA cambia
        assert cfg["paused_until"] > time.time()
        assert "pérdida diaria" in cfg["pause_reason"]
        assert cfg["daily_loss_usd"] >= 10.0
        assert any("pausad" in msg.lower() for msg in tg_calls)


# ── Bloqueo durante la pausa / reanudación automática a las 24h ────────────

class TestPauseBlocksAndAutoResumes:
    def test_check_risk_before_trade_blocks_during_pause(self, isolated_executor):
        cfg = _live_mode_cfg()
        cfg["paused_until"] = time.time() + 3600
        cfg["pause_reason"] = "prueba"
        auto_executor._save_config(cfg)

        allowed, reason = auto_executor.check_risk_before_trade(10.0)
        assert allowed is False
        assert "pausad" in reason.lower()

    def test_open_positions_are_never_touched_by_a_pause(self, isolated_executor):
        """Una pausa activa no debe tocar las posiciones ya abiertas -- solo
        bloquea ENTRADAS nuevas vía check_risk_before_trade(). No hay
        ningún código en esta corrección que cierre o modifique una
        LiveTrade al activar/mantener una pausa."""
        auto_executor._save_config(_live_mode_cfg())
        pid = _new_position_id()
        auto_executor.record_live_trade_open(
            position_id=pid, order_id="ORD-1", symbol="TSLA", direction="buy",
            amount_usd=50.0, entry_price=300.0, stop_loss=295.0,
            take_profit=310.0, proposal_id="PROP-1",
        )
        for _ in range(3):
            auto_executor.record_trade_result(
                -10.0, is_paper=False, trade_id=f"LIVE-{_new_position_id()}"
            )
        assert auto_executor.get_config()["paused_until"] > 0
        # La posición sigue "open" -- nada de esta corrección la tocó.
        trades = auto_executor.get_live_trades(limit=10)
        assert [t for t in trades if t.position_id == pid][0].status == "open"

    def test_pause_auto_lifts_after_24h_and_resets_counters(
        self, isolated_executor, tg_calls, monkeypatch
    ):
        cfg = _live_mode_cfg(consecutive_losses=3, daily_loss_usd=15.0)
        cfg["paused_until"] = time.time() - 1  # ya vencida
        cfg["pause_reason"] = "prueba vencida"
        auto_executor._save_config(cfg)
        # Aislar ESTE test de max_positions_open (chequeo aparte, cubierto
        # en TestMaxPositionsOpen) -- sin esto, check_risk_before_trade en
        # LIVE intentaría una llamada real al bróker.
        monkeypatch.setattr(
            "connectors.etoro.etoro_client.get_portfolio",
            lambda: {"success": True, "n_positions": 0, "positions": []},
        )

        allowed, reason = auto_executor.check_risk_before_trade(10.0)

        fresh = auto_executor.get_config()
        assert fresh["paused_until"] == 0
        assert fresh["pause_reason"] == ""
        assert fresh["consecutive_losses"] == 0
        assert fresh["daily_loss_usd"] == 0.0
        assert fresh["mode"] == AutoMode.LIVE.value  # seguía en LIVE todo el tiempo
        assert any("reanudad" in msg.lower() or "levant" in msg.lower() for msg in tg_calls)
        # Sin ninguna otra condición de bloqueo, la entrada ya se permite
        # en la MISMA llamada que levantó la pausa.
        assert allowed is True

    def test_pause_not_yet_expired_is_not_lifted(self, isolated_executor):
        cfg = _live_mode_cfg(consecutive_losses=3)
        cfg["paused_until"] = time.time() + 3600  # vigente todavía
        cfg["pause_reason"] = "prueba vigente"
        auto_executor._save_config(cfg)

        auto_executor.check_risk_before_trade(10.0)

        fresh = auto_executor.get_config()
        assert fresh["paused_until"] > time.time()
        assert fresh["pause_reason"] == "prueba vigente"
        assert fresh["consecutive_losses"] == 3  # no se reinició antes de tiempo


# ── HALT manual sigue siendo independiente de la pausa automática ─────────

class TestHaltIndependentFromPause:
    def test_halt_is_never_lifted_by_pause_expiry(self, isolated_executor):
        cfg = _live_mode_cfg(halt=True)
        cfg["paused_until"] = time.time() - 1  # pausa automática ya vencida
        cfg["pause_reason"] = "prueba"
        auto_executor._save_config(cfg)

        allowed, reason = auto_executor.check_risk_before_trade(10.0)

        assert allowed is False
        assert "HALT" in reason
        fresh = auto_executor.get_config()
        assert fresh["halt"] is True  # _maybe_lift_expired_pause nunca toca halt

    def test_pause_expiring_does_not_auto_unhalt(self, isolated_executor):
        cfg = _live_mode_cfg(halt=True, consecutive_losses=3)
        cfg["paused_until"] = time.time() - 1
        auto_executor._save_config(cfg)
        auto_executor._maybe_lift_expired_pause()
        assert auto_executor.get_config()["halt"] is True


# ── La pausa sobrevive un reinicio de procesos ─────────────────────────────

class TestPauseSurvivesRestart:
    def test_pause_persists_across_a_simulated_restart(self, isolated_executor):
        """paused_until/pause_reason viven en el mismo JSON persistido que
        el resto de la config -- un proceso nuevo (simulado aquí releyendo
        desde una instancia de módulo "fresca", sin ningún estado en
        memoria previo) los lee del disco tal cual quedaron."""
        cfg = _live_mode_cfg()
        cfg["paused_until"] = time.time() + 3600
        cfg["pause_reason"] = "activa antes del reinicio"
        auto_executor._save_config(cfg)

        # Simula un proceso nuevo: _load_config() vuelve a leer del disco,
        # sin ningún estado en memoria heredado del proceso anterior.
        reloaded = auto_executor._load_config()
        assert reloaded["paused_until"] > time.time()
        assert reloaded["pause_reason"] == "activa antes del reinicio"

        # Y sigue bloqueando entradas después de ese "reinicio".
        allowed, _ = auto_executor.check_risk_before_trade(10.0)
        assert allowed is False

    def test_restart_does_not_shorten_an_active_pause(self, isolated_executor):
        cfg = _live_mode_cfg()
        original_paused_until = time.time() + 3600
        cfg["paused_until"] = original_paused_until
        cfg["pause_reason"] = "no debe acortarse"
        auto_executor._save_config(cfg)

        # Nada en el arranque (_load_config, check_risk_before_trade) debe
        # recortar ni extender paused_until por su cuenta.
        auto_executor.check_risk_before_trade(10.0)
        assert auto_executor.get_config()["paused_until"] == original_paused_until


# ── 5: max_positions_open bloquea ───────────────────────────────────────────

class TestMaxPositionsOpen:
    def test_paper_blocks_when_at_limit(self, isolated_executor, monkeypatch):
        auto_executor._save_config(dict(auto_executor.DEFAULTS, mode=AutoMode.PAPER.value, max_positions_open=2))
        for i in range(2):
            auto_executor.record_paper_trade(
                symbol="AAPL", direction="buy", amount_usd=10.0,
                entry_price=100.0, stop_loss=98.0, take_profit=105.0,
                proposal_id=f"PROP-{i}",
            )
        allowed, reason = auto_executor.check_risk_before_trade(10.0)
        assert allowed is False
        assert "posiciones simultáneas" in reason

    def test_paper_allows_below_limit(self, isolated_executor):
        auto_executor._save_config(dict(auto_executor.DEFAULTS, mode=AutoMode.PAPER.value, max_positions_open=2))
        auto_executor.record_paper_trade(
            symbol="AAPL", direction="buy", amount_usd=10.0,
            entry_price=100.0, stop_loss=98.0, take_profit=105.0,
            proposal_id="PROP-1",
        )
        allowed, reason = auto_executor.check_risk_before_trade(10.0)
        assert allowed is True
        assert reason == ""

    def test_live_counts_broker_positions_not_local_log(self, isolated_executor, monkeypatch):
        """En LIVE, el conteo es contra el bróker real, no contra el log
        propio -- aunque el log local diga 0 abiertas, si el bróker dice 2
        y el techo es 2, bloquea."""
        auto_executor._save_config(_live_mode_cfg(max_positions_open=2))
        fake_portfolio = {"success": True, "n_positions": 2, "positions": []}
        monkeypatch.setattr(
            "connectors.etoro.etoro_client.get_portfolio", lambda: fake_portfolio
        )
        allowed, reason = auto_executor.check_risk_before_trade(10.0)
        assert allowed is False
        assert "posiciones simultáneas" in reason

    def test_live_allows_when_broker_below_limit(self, isolated_executor, monkeypatch):
        auto_executor._save_config(_live_mode_cfg(max_positions_open=2))
        fake_portfolio = {"success": True, "n_positions": 1, "positions": []}
        monkeypatch.setattr(
            "connectors.etoro.etoro_client.get_portfolio", lambda: fake_portfolio
        )
        allowed, reason = auto_executor.check_risk_before_trade(10.0)
        assert allowed is True


# ── 6: fallo del bróker bloquea en LIVE (fail closed) ───────────────────────

class TestBrokerFailureFailsClosed:
    def test_broker_query_failure_blocks_live_trade(self, isolated_executor, monkeypatch):
        auto_executor._save_config(_live_mode_cfg())
        fake_portfolio = {"success": False, "error": "timeout"}
        monkeypatch.setattr(
            "connectors.etoro.etoro_client.get_portfolio", lambda: fake_portfolio
        )
        allowed, reason = auto_executor.check_risk_before_trade(10.0)
        assert allowed is False
        assert "bróker" in reason.lower() or "broker" in reason.lower()

    def test_broker_query_exception_blocks_live_trade(self, isolated_executor, monkeypatch):
        auto_executor._save_config(_live_mode_cfg())

        def _raise():
            raise ConnectionError("boom")
        monkeypatch.setattr("connectors.etoro.etoro_client.get_portfolio", _raise)
        allowed, reason = auto_executor.check_risk_before_trade(10.0)
        assert allowed is False

    def test_paper_mode_unaffected_by_broker_failure(self, isolated_executor, monkeypatch):
        """No regresión: PAPER nunca consulta al bróker para este chequeo,
        así que un bróker roto no debe bloquear PAPER."""
        auto_executor._save_config(dict(auto_executor.DEFAULTS, mode=AutoMode.PAPER.value))

        def _raise():
            raise ConnectionError("boom")
        monkeypatch.setattr("connectors.etoro.etoro_client.get_portfolio", _raise)
        allowed, reason = auto_executor.check_risk_before_trade(10.0)
        assert allowed is True


# ── 7: PnL no disponible no se registra ─────────────────────────────────────

class TestClosedPnlPending:
    """Corrección 2026-09-26 (segunda vuelta del PR #134): reemplaza el
    diseño anterior ("closed_pnl_pending sin registrar nada") por una
    estimación de peor caso inmediata, marcada pnl_source="estimated"."""

    def _open_trade(self, **overrides):
        pid = _new_position_id()
        kwargs = dict(
            position_id=pid, order_id="ORD-1", symbol="TSLA", direction="buy",
            amount_usd=50.0, entry_price=300.0, stop_loss=295.0,
            take_profit=310.0, proposal_id="PROP-1",
        )
        kwargs.update(overrides)
        auto_executor.record_live_trade_open(**kwargs)
        return pid

    def test_disappeared_position_gets_worst_case_estimate_recorded(
        self, isolated_executor, monkeypatch, tg_calls
    ):
        from connectors.etoro import position_manager

        auto_executor._save_config(_live_mode_cfg())
        pid = self._open_trade()

        # El bróker ya no reporta esta posición como abierta.
        monkeypatch.setattr(
            "connectors.etoro.etoro_client.get_portfolio",
            lambda: {"success": True, "n_positions": 0, "positions": []},
        )

        actions = position_manager.check_live_positions_closed()

        assert len(actions) == 1
        assert actions[0]["status"] == "closed_pnl_pending"
        # Estimación de peor caso: amount_usd * |entry-stop|/entry
        # = 50 * (300-295)/300 = 0.8333...
        assert actions[0]["pnl_estimate_usd"] == pytest.approx(-0.83, abs=0.01)

        trades = auto_executor.get_live_trades(limit=10)
        assert trades[0].pnl_source == "estimated"
        assert trades[0].status == "closed_loss"
        assert trades[0].pnl_usd < 0
        assert any("pnl" in msg.lower() or "estimaci" in msg.lower() for msg in tg_calls)

        # Y SÍ cuenta para los contadores de riesgo -- a diferencia del
        # diseño anterior, que nunca llamaba a nada.
        cfg = auto_executor.get_config()
        assert cfg["consecutive_losses"] == 1

    def test_position_still_open_at_broker_is_left_alone(self, isolated_executor, monkeypatch):
        from connectors.etoro import position_manager

        auto_executor._save_config(_live_mode_cfg())
        pid = self._open_trade()
        monkeypatch.setattr(
            "connectors.etoro.etoro_client.get_portfolio",
            lambda: {"success": True, "n_positions": 1, "positions": [{"positionID": pid}]},
        )
        actions = position_manager.check_live_positions_closed()
        assert actions == []
        trades = auto_executor.get_live_trades(limit=10)
        assert trades[0].status == "open"
        assert trades[0].pnl_source is None

    def test_zero_paper_trades_still_detects_live_close(self, isolated_executor, monkeypatch):
        """Bug real encontrado 2026-09-26 (cuarta vuelta del PR #134):
        check_open_positions() salía antes de llegar a la detección LIVE
        cuando no había trades PAPER abiertos -- el caso normal en modo
        LIVE puro. Sin ningún trade PAPER, la detección LIVE debe correr
        igual."""
        from connectors.etoro import position_manager

        auto_executor._save_config(_live_mode_cfg())
        assert auto_executor.get_paper_trades(limit=100) == []  # cero PAPER abiertos

        pid = self._open_trade()
        monkeypatch.setattr(
            "connectors.etoro.etoro_client.get_portfolio",
            lambda: {"success": True, "n_positions": 0, "positions": []},
        )

        actions = position_manager.check_open_positions()  # la función completa, no solo la LIVE

        assert len(actions) == 1
        assert actions[0]["trade_id"] == f"LIVE-{pid}"
        trades = auto_executor.get_live_trades(limit=10)
        assert trades[0].pnl_source == "estimated"


# ── record_live_trade_result: estimado vs real, sin sumar ─────────────────

class TestRecordLiveTradeResultPrecedence:
    def test_duplicate_estimate_ignored(self, isolated_executor):
        auto_executor._save_config(_live_mode_cfg())
        pid = _new_position_id()
        auto_executor.record_live_trade_open(
            position_id=pid, order_id="O1", symbol="TSLA", direction="buy",
            amount_usd=50.0, entry_price=300.0, stop_loss=295.0,
            take_profit=310.0, proposal_id="P1",
        )
        tid = f"LIVE-{pid}"

        changed1 = auto_executor.record_live_trade_result(tid, -5.0, source="estimated")
        changed2 = auto_executor.record_live_trade_result(tid, -5.0, source="estimated")

        assert changed1 is True
        assert changed2 is False  # duplicada, ignorada
        trade = auto_executor.get_live_trades(limit=10)[0]
        assert trade.pnl_usd == -5.0  # no se duplicó/sumó

    def test_real_pnl_replaces_estimate_without_summing(self, isolated_executor):
        auto_executor._save_config(_live_mode_cfg())
        pid = _new_position_id()
        auto_executor.record_live_trade_open(
            position_id=pid, order_id="O1", symbol="TSLA", direction="buy",
            amount_usd=50.0, entry_price=300.0, stop_loss=295.0,
            take_profit=310.0, proposal_id="P1",
        )
        tid = f"LIVE-{pid}"

        auto_executor.record_live_trade_result(tid, -5.0, source="estimated")
        changed = auto_executor.record_live_trade_result(tid, -3.0, source="real")

        assert changed is True
        trade = auto_executor.get_live_trades(limit=10)[0]
        assert trade.pnl_usd == -3.0  # reemplazó, NO -5 + -3 = -8
        assert trade.pnl_source == "real"

    def test_real_pnl_is_final_nothing_overwrites_it(self, isolated_executor):
        auto_executor._save_config(_live_mode_cfg())
        pid = _new_position_id()
        auto_executor.record_live_trade_open(
            position_id=pid, order_id="O1", symbol="TSLA", direction="buy",
            amount_usd=50.0, entry_price=300.0, stop_loss=295.0,
            take_profit=310.0, proposal_id="P1",
        )
        tid = f"LIVE-{pid}"
        auto_executor.record_live_trade_result(tid, -3.0, source="real")

        changed = auto_executor.record_live_trade_result(tid, -99.0, source="estimated")

        assert changed is False
        trade = auto_executor.get_live_trades(limit=10)[0]
        assert trade.pnl_usd == -3.0  # intacto

    def test_manual_real_pnl_shortcut_uses_source_real(self, isolated_executor):
        auto_executor._save_config(_live_mode_cfg())
        pid = _new_position_id()
        auto_executor.record_live_trade_open(
            position_id=pid, order_id="O1", symbol="TSLA", direction="buy",
            amount_usd=50.0, entry_price=300.0, stop_loss=295.0,
            take_profit=310.0, proposal_id="P1",
        )
        changed = auto_executor.record_live_trade_real_pnl(f"LIVE-{pid}", 4.5)
        assert changed is True
        trade = auto_executor.get_live_trades(limit=10)[0]
        assert trade.pnl_source == "real"
        assert trade.pnl_usd == 4.5


# ── Recálculo desde cero: día, racha, y su interacción con la pausa ────────

class TestRecomputedRiskCounters:
    def _open_and_close(self, symbol, pnl, closed_at, source="real"):
        pid = _new_position_id()
        auto_executor.record_live_trade_open(
            position_id=pid, order_id="O", symbol=symbol, direction="buy",
            amount_usd=50.0, entry_price=300.0, stop_loss=295.0,
            take_profit=310.0, proposal_id="P",
        )
        tid = f"LIVE-{pid}"
        auto_executor.record_live_trade_result(tid, pnl, source=source, closed_at=closed_at)
        return tid

    def test_correcting_yesterdays_close_does_not_affect_todays_daily_loss(self, isolated_executor):
        auto_executor._save_config(_live_mode_cfg(max_consecutive_losses=99, max_daily_loss_usd=999))
        yesterday = time.time() - 90000  # >24h atrás, otro día calendario
        today = time.time()

        tid_yesterday = self._open_and_close("AAPL", -1.0, closed_at=yesterday, source="estimated")
        self._open_and_close("TSLA", -7.0, closed_at=today)

        cfg = auto_executor.get_config()
        assert cfg["daily_loss_usd"] == pytest.approx(7.0)  # solo la de hoy

        # Corrección REAL del cierre de ayer (reemplaza la estimación) --
        # sigue siendo de ayer, no debe sumarse ni restarse del daily_loss
        # de HOY, sin importar cuánto cambie el número corregido.
        auto_executor.record_live_trade_result(
            tid_yesterday, -50.0, source="real", closed_at=yesterday,
        )

        cfg = auto_executor.get_config()
        assert cfg["daily_loss_usd"] == pytest.approx(7.0)  # sin cambios

    def test_streak_reconstructed_correctly_after_a_correction(self, isolated_executor):
        auto_executor._save_config(_live_mode_cfg(max_consecutive_losses=99, max_daily_loss_usd=999))
        now = time.time()
        t1 = self._open_and_close("AAPL", -5.0, closed_at=now - 30, source="estimated")
        t2 = self._open_and_close("TSLA", -5.0, closed_at=now - 20, source="estimated")
        t3 = self._open_and_close("NVDA", -5.0, closed_at=now - 10, source="estimated")

        assert auto_executor.get_config()["consecutive_losses"] == 3

        # Corrección: la del MEDIO (t2) en realidad fue una ganancia.
        auto_executor.record_live_trade_result(t2, 8.0, source="real", closed_at=now - 20)

        # Secuencia cronológica ahora: loss(t1), win(t2), loss(t3) -- la
        # racha ACTUAL (desde el cierre más reciente hacia atrás) es 1.
        assert auto_executor.get_config()["consecutive_losses"] == 1

    def test_improving_correction_does_not_lift_an_active_pause(self, isolated_executor):
        auto_executor._save_config(_live_mode_cfg(max_consecutive_losses=3, max_daily_loss_usd=999))
        now = time.time()
        t1 = self._open_and_close("AAPL", -5.0, closed_at=now - 30, source="estimated")
        self._open_and_close("TSLA", -5.0, closed_at=now - 20, source="estimated")
        self._open_and_close("NVDA", -5.0, closed_at=now - 10, source="estimated")

        cfg = auto_executor.get_config()
        assert cfg["paused_until"] > 0
        paused_until_before = cfg["paused_until"]

        # Corrección que MEJORA (t1 en realidad fue ganancia) -- ya no hay
        # 3 pérdidas consecutivas, pero la pausa activa NO debe levantarse.
        auto_executor.record_live_trade_result(t1, 3.0, source="real", closed_at=now - 30)

        cfg = auto_executor.get_config()
        assert cfg["paused_until"] == paused_until_before  # sin cambios
        assert cfg["consecutive_losses"] < 3  # el número sí se corrigió

    def test_worsening_correction_activates_a_new_pause(self, isolated_executor):
        auto_executor._save_config(_live_mode_cfg(max_consecutive_losses=3, max_daily_loss_usd=999))
        now = time.time()
        self._open_and_close("AAPL", -5.0, closed_at=now - 30, source="estimated")
        self._open_and_close("TSLA", 8.0, closed_at=now - 20, source="estimated")  # gana, rompe la racha
        self._open_and_close("NVDA", -5.0, closed_at=now - 10, source="estimated")

        assert auto_executor.get_config()["paused_until"] == 0  # racha actual=1, sin pausa

        # Corrección que EMPEORA: la ganancia del medio en realidad fue
        # pérdida -> secuencia queda loss/loss/loss -> racha reconstruida
        # = 3 -> cruza el límite -> se activa la pausa.
        trades = auto_executor.get_live_trades(limit=10)
        middle = next(t for t in trades if t.symbol == "TSLA")
        auto_executor.record_live_trade_result(
            middle.trade_id, -2.0, source="real", closed_at=now - 20,
        )

        cfg = auto_executor.get_config()
        assert cfg["consecutive_losses"] == 3
        assert cfg["paused_until"] > 0  # la corrección que empeora SÍ activa


# ── Fail-closed: bloqueo mientras haya estimaciones sin confirmar ─────────

class TestFailClosedWhilePending:
    def test_blocks_new_entries_while_any_estimate_pending(self, isolated_executor):
        auto_executor._save_config(_live_mode_cfg())
        pid = _new_position_id()
        auto_executor.record_live_trade_open(
            position_id=pid, order_id="O1", symbol="TSLA", direction="buy",
            amount_usd=50.0, entry_price=300.0, stop_loss=295.0,
            take_profit=310.0, proposal_id="P1",
        )
        auto_executor.record_live_trade_result(f"LIVE-{pid}", -0.8, source="estimated")

        allowed, reason = auto_executor.check_risk_before_trade(10.0)
        assert allowed is False
        assert "estimad" in reason.lower()

    def test_resolving_the_estimate_unblocks(self, isolated_executor, monkeypatch):
        auto_executor._save_config(_live_mode_cfg())
        pid = _new_position_id()
        auto_executor.record_live_trade_open(
            position_id=pid, order_id="O1", symbol="TSLA", direction="buy",
            amount_usd=50.0, entry_price=300.0, stop_loss=295.0,
            take_profit=310.0, proposal_id="P1",
        )
        auto_executor.record_live_trade_result(f"LIVE-{pid}", -0.8, source="estimated")
        assert auto_executor.check_risk_before_trade(10.0)[0] is False

        auto_executor.record_live_trade_real_pnl(f"LIVE-{pid}", -0.8)

        # También aislar del chequeo de max_positions_open (no es lo que
        # este test verifica).
        monkeypatch.setattr(
            "connectors.etoro.etoro_client.get_portfolio",
            lambda: {"success": True, "n_positions": 0, "positions": []},
        )
        allowed, reason = auto_executor.check_risk_before_trade(10.0)
        assert allowed is True, reason


# ── 8: valores inválidos en Telegram rechazados ─────────────────────────────

class TestValidateRiskLimit:
    @pytest.mark.parametrize("key,value", [
        ("max_position_usd", 0),
        ("max_position_usd", -5),
        ("max_position_usd", auto_executor.MAX_POSITION_USD_CEILING + 0.01),
        ("max_daily_loss_usd", 0),
        ("max_daily_loss_usd", auto_executor.MAX_DAILY_LOSS_USD_CEILING + 0.01),
        ("stop_loss_pct", 0.0),
        ("stop_loss_pct", 10.01),
        ("max_consecutive_losses", 0),
        ("max_consecutive_losses", 11),
        ("max_positions_open", 0),
        ("max_positions_open", 6),
        ("min_paper_signals", 9),
    ])
    def test_out_of_range_rejected(self, key, value):
        valid, coerced, error = auto_executor.validate_risk_limit(key, value)
        assert valid is False
        assert coerced is None
        assert error

    @pytest.mark.parametrize("key,value", [
        ("max_position_usd", 1),
        ("max_position_usd", auto_executor.MAX_POSITION_USD_CEILING),
        ("max_daily_loss_usd", auto_executor.MAX_DAILY_LOSS_USD_CEILING),
        ("stop_loss_pct", 1.5),
        ("max_consecutive_losses", 3),
        ("max_positions_open", 5),
        ("min_paper_signals", 30),
    ])
    def test_in_range_accepted(self, key, value):
        valid, coerced, error = auto_executor.validate_risk_limit(key, value)
        assert valid is True
        assert error == ""

    def test_ceiling_is_not_configurable_by_the_validator_itself(self):
        """El techo es una constante de módulo -- no un valor de config
        que este validador pudiera aceptar cambiar."""
        assert auto_executor.MAX_POSITION_USD_CEILING == 200.0
        assert auto_executor.MAX_DAILY_LOSS_USD_CEILING == 50.0


# ── PnL real desde el historial de eToro (cuarta vuelta del PR #134) ──────
# GET /trading/info/trade/history -- confirmado en vivo, solo lectura, antes
# de escribir esta wiring: devuelve una lista de cierres reales con
# netProfit/closeTimestamp/positionId. Estos tests mockean etoro_client._request
# o get_trade_history directamente -- ninguno pega a la red real.

class TestGetTradeHistory:
    def test_calls_the_documented_endpoint_with_min_date(self, monkeypatch):
        from connectors.etoro import etoro_client

        captured = {}

        def _fake_request(method, endpoint, body=None, params=None):
            captured["method"] = method
            captured["endpoint"] = endpoint
            captured["params"] = params
            return {"success": True, "data": [], "latency_ms": 1.0}

        monkeypatch.setattr(etoro_client, "_request", _fake_request)
        result = etoro_client.get_trade_history(min_date="2026-08-01")

        assert result["success"] is True
        assert result["trades"] == []
        assert captured["method"] == "GET"
        assert captured["endpoint"] == "/trading/info/trade/history"
        assert captured["params"] == {"minDate": "2026-08-01"}

    def test_page_and_page_size_are_optional_and_passed_through(self, monkeypatch):
        from connectors.etoro import etoro_client

        captured = {}
        monkeypatch.setattr(
            etoro_client, "_request",
            lambda method, endpoint, body=None, params=None: (
                captured.update(params=params) or {"success": True, "data": [], "latency_ms": 1.0}
            ),
        )
        etoro_client.get_trade_history(min_date="2026-08-01", page=2, page_size=50)
        assert captured["params"] == {"minDate": "2026-08-01", "page": 2, "pageSize": 50}

    def test_propagates_failure_as_is(self, monkeypatch):
        from connectors.etoro import etoro_client

        monkeypatch.setattr(
            etoro_client, "_request",
            lambda *a, **k: {"success": False, "error": "HTTP 404", "status": 404},
        )
        result = etoro_client.get_trade_history(min_date="2026-08-01")
        assert result["success"] is False
        assert result["error"] == "HTTP 404"

    def test_respects_the_60_per_minute_rate_limit(self, monkeypatch):
        """No debe superar 60 llamadas por ventana de 60s -- la llamada 61
        debe dormir en vez de disparar directo."""
        from connectors.etoro import etoro_client

        monkeypatch.setattr(etoro_client, "_trade_history_call_times", [])
        monkeypatch.setattr(
            etoro_client, "_request",
            lambda *a, **k: {"success": True, "data": [], "latency_ms": 1.0},
        )
        fake_now = [1_000_000.0]
        monkeypatch.setattr(etoro_client.time, "time", lambda: fake_now[0])
        slept = []
        monkeypatch.setattr(etoro_client.time, "sleep", lambda s: slept.append(s))

        for _ in range(60):
            etoro_client.get_trade_history(min_date="2026-08-01")
        assert slept == []  # las primeras 60 no deben dormir nada

        etoro_client.get_trade_history(min_date="2026-08-01")  # la 61ª
        assert len(slept) == 1
        assert slept[0] > 0


class TestParseEtoroTimestamp:
    def test_parses_variable_precision_fractional_seconds(self):
        from connectors.etoro.etoro_client import parse_etoro_timestamp
        # eToro real -- 1 dígito de fracción -- fromisoformat rechaza esto
        # en Python 3.9, por eso existe este parser en vez de usarlo directo.
        epoch = parse_etoro_timestamp("2026-09-23T19:46:25.2Z")
        assert epoch is not None
        assert epoch == pytest.approx(1790192785.2, abs=0.01)

    def test_parses_without_fractional_seconds(self):
        from connectors.etoro.etoro_client import parse_etoro_timestamp
        assert parse_etoro_timestamp("2026-09-23T19:46:25Z") is not None

    def test_returns_none_for_missing_or_garbage(self):
        from connectors.etoro.etoro_client import parse_etoro_timestamp
        assert parse_etoro_timestamp(None) is None
        assert parse_etoro_timestamp("") is None
        assert parse_etoro_timestamp("not-a-timestamp") is None


class TestResolvePendingEstimatesFromRealHistory:
    """check_live_positions_closed() intenta reemplazar cada estimación
    pendiente por el PnL real del historial de eToro ANTES de buscar
    cierres nuevos (cuarta vuelta del PR #134)."""

    def _open_trade(self, **overrides):
        pid = _new_position_id()
        kwargs = dict(
            position_id=pid, order_id="ORD-1", symbol="TSLA", direction="buy",
            amount_usd=50.0, entry_price=300.0, stop_loss=295.0,
            take_profit=310.0, proposal_id="PROP-1",
        )
        kwargs.update(overrides)
        auto_executor.record_live_trade_open(**kwargs)
        return pid

    def test_real_pnl_found_in_history_replaces_the_estimate(
        self, isolated_executor, monkeypatch, tg_calls
    ):
        from connectors.etoro import position_manager

        auto_executor._save_config(_live_mode_cfg())
        pid = self._open_trade()
        auto_executor.record_live_trade_result(
            f"LIVE-{pid}", -0.83, source="estimated",
        )

        # positionId en la respuesta del historial se compara como string
        # (str(h.get("positionId"))) contra trade.position_id.
        monkeypatch.setattr(
            "connectors.etoro.etoro_client.get_trade_history",
            lambda min_date, page=None, page_size=None: {
                "success": True,
                "trades": [{
                    "positionId": pid,
                    "netProfit": -1.10,
                    "closeTimestamp": "2026-09-23T19:46:25.2Z",
                }],
            },
        )
        # También aislar el resto de check_live_positions_closed() (no hay
        # trades 'open' después de la estimación, así que ni siquiera
        # debería llegar a consultar el portfolio -- pero por las dudas).
        monkeypatch.setattr(
            "connectors.etoro.etoro_client.get_portfolio",
            lambda: {"success": True, "n_positions": 0, "positions": []},
        )

        actions = position_manager.check_live_positions_closed()

        real_actions = [a for a in actions if a.get("reason") == "real_pnl_from_broker_history"]
        assert len(real_actions) == 1
        assert real_actions[0]["pnl_real_usd"] == -1.10

        trade = auto_executor.get_live_trades(limit=10)[0]
        assert trade.pnl_source == "real"
        assert trade.pnl_usd == -1.10
        assert any("real" in msg.lower() for msg in tg_calls)

    def test_not_found_yet_leaves_the_estimate_untouched_for_next_cycle(
        self, isolated_executor, monkeypatch
    ):
        from connectors.etoro import position_manager

        auto_executor._save_config(_live_mode_cfg())
        pid = self._open_trade()
        auto_executor.record_live_trade_result(
            f"LIVE-{pid}", -0.83, source="estimated",
        )

        monkeypatch.setattr(
            "connectors.etoro.etoro_client.get_trade_history",
            lambda min_date, page=None, page_size=None: {"success": True, "trades": []},
        )
        monkeypatch.setattr(
            "connectors.etoro.etoro_client.get_portfolio",
            lambda: {"success": True, "n_positions": 0, "positions": []},
        )

        actions = position_manager.check_live_positions_closed()

        assert not any(a.get("reason") == "real_pnl_from_broker_history" for a in actions)
        trade = auto_executor.get_live_trades(limit=10)[0]
        assert trade.pnl_source == "estimated"  # intacta, se reintenta después
        assert trade.pnl_usd == -0.83

    def test_history_call_failure_does_not_crash_and_leaves_estimate(
        self, isolated_executor, monkeypatch
    ):
        from connectors.etoro import position_manager

        auto_executor._save_config(_live_mode_cfg())
        pid = self._open_trade()
        auto_executor.record_live_trade_result(
            f"LIVE-{pid}", -0.83, source="estimated",
        )

        monkeypatch.setattr(
            "connectors.etoro.etoro_client.get_trade_history",
            lambda min_date, page=None, page_size=None: {
                "success": False, "error": "HTTP 500",
            },
        )
        monkeypatch.setattr(
            "connectors.etoro.etoro_client.get_portfolio",
            lambda: {"success": True, "n_positions": 0, "positions": []},
        )

        actions = position_manager.check_live_positions_closed()  # no debe tirar excepción

        assert actions == []
        trade = auto_executor.get_live_trades(limit=10)[0]
        assert trade.pnl_source == "estimated"


# ── Racha reconstruida SOLO desde el último levantamiento de pausa ────────
# (cuarta vuelta del PR #134): sin este corte, levantar una pausa de 24h no
# rompía la racha que la causó -- el primer recálculo posterior veía la
# misma racha completa y reactivaba la pausa de inmediato.

class TestStreakResetAfterPauseLift:
    def _open_and_close(self, symbol, pnl, closed_at, source="real"):
        pid = _new_position_id()
        auto_executor.record_live_trade_open(
            position_id=pid, order_id="O", symbol=symbol, direction="buy",
            amount_usd=50.0, entry_price=300.0, stop_loss=295.0,
            take_profit=310.0, proposal_id="P",
        )
        tid = f"LIVE-{pid}"
        auto_executor.record_live_trade_result(tid, pnl, source=source, closed_at=closed_at)
        return tid

    def test_maybe_lift_expired_pause_stamps_pause_lifted_at(self, isolated_executor):
        cfg = _live_mode_cfg(consecutive_losses=3)
        cfg["paused_until"] = time.time() - 1  # vencida
        cfg["pause_reason"] = "prueba"
        auto_executor._save_config(cfg)

        before = time.time()
        auto_executor._maybe_lift_expired_pause()
        after = time.time()

        fresh = auto_executor.get_config()
        assert before <= fresh["pause_lifted_at"] <= after

    def test_streak_from_before_the_lift_does_not_immediately_repause(self, isolated_executor):
        """3 pérdidas activan la pausa; la pausa vence y se levanta sin que
        medie ninguna ganancia; el siguiente cierre (otra pérdida) NO debe
        ver una racha de 4 -- la racha se cuenta solo desde pause_lifted_at."""
        cfg = _live_mode_cfg(max_consecutive_losses=3, max_daily_loss_usd=999)
        auto_executor._save_config(cfg)
        now = time.time()
        self._open_and_close("AAPL", -5.0, closed_at=now - 300)
        self._open_and_close("TSLA", -5.0, closed_at=now - 200)
        self._open_and_close("NVDA", -5.0, closed_at=now - 100)

        cfg = auto_executor.get_config()
        assert cfg["consecutive_losses"] == 3
        assert cfg["paused_until"] > 0

        # La pausa vence y se levanta sola -- sin intervención manual, sin
        # ninguna ganancia intermedia (mismo escenario del creador).
        fresh = auto_executor.get_config()
        fresh["paused_until"] = time.time() - 1
        auto_executor._save_config(fresh)
        auto_executor._maybe_lift_expired_pause()

        lifted_cfg = auto_executor.get_config()
        assert lifted_cfg["paused_until"] == 0
        assert lifted_cfg["pause_lifted_at"] > 0

        # Un cierre nuevo, DESPUÉS de la reanudación, también perdedor.
        self._open_and_close("MSFT", -5.0, closed_at=time.time())

        final_cfg = auto_executor.get_config()
        assert final_cfg["consecutive_losses"] == 1  # no 4 -- la racha vieja no cuenta
        assert final_cfg["paused_until"] == 0  # no se reactivó de inmediato

    def test_daily_loss_usd_is_not_reset_by_the_cutoff_only_by_calendar_day(
        self, isolated_executor
    ):
        """Pedido explícito: pause_lifted_at NO debe afectar daily_loss_usd
        -- sigue siendo estrictamente por día calendario."""
        cfg = _live_mode_cfg(max_consecutive_losses=99, max_daily_loss_usd=999)
        auto_executor._save_config(cfg)
        now = time.time()

        # Pérdida de HOY, antes de que exista ninguna pausa.
        self._open_and_close("AAPL", -5.0, closed_at=now - 100)
        assert auto_executor.get_config()["daily_loss_usd"] == pytest.approx(5.0)

        # Se activa y levanta una pausa manualmente (sin relación con esta
        # pérdida -- simula, p. ej., el cortacircuito de racha disparando
        # y venciendo aparte).
        fresh = auto_executor.get_config()
        fresh["pause_lifted_at"] = now - 50  # posterior a la pérdida de arriba
        auto_executor._save_config(fresh)

        # Otra pérdida de HOY, después del corte de pause_lifted_at.
        self._open_and_close("TSLA", -3.0, closed_at=now - 10)

        # Las DOS pérdidas de hoy cuentan para daily_loss_usd -- el corte
        # de pause_lifted_at es exclusivo de consecutive_losses.
        assert auto_executor.get_config()["daily_loss_usd"] == pytest.approx(8.0)


class TestStatusPanelShowsLiveRiskState:
    """Pedido explícito antes del reinicio: el panel de /vx market auto
    status debe mostrar la pérdida diaria ACTUAL (no solo el techo), la
    racha y el estado de pausa. Corrección 2026-09-26 (quinta vuelta del
    PR #134) -- antes faltaba la pérdida diaria actual por completo."""

    def test_current_daily_loss_appears_not_just_the_ceiling(self, isolated_executor):
        auto_executor._save_config(_live_mode_cfg(daily_loss_usd=12.5, max_daily_loss_usd=50.0))
        panel = auto_executor.format_auto_status()
        assert "12.5" in panel or "12.50" in panel
        assert "50" in panel

    def test_consecutive_losses_streak_appears(self, isolated_executor):
        auto_executor._save_config(_live_mode_cfg(consecutive_losses=2, max_consecutive_losses=3))
        panel = auto_executor.format_auto_status()
        assert "2/3" in panel

    def test_pause_state_appears_while_active(self, isolated_executor):
        cfg = _live_mode_cfg()
        cfg["paused_until"] = time.time() + 3600
        cfg["pause_reason"] = "prueba de panel"
        auto_executor._save_config(cfg)
        panel = auto_executor.format_auto_status()
        assert "PAUSADO" in panel
        assert "prueba de panel" in panel


class TestRecomputeLiveRiskCountersCutoffUnit:
    """Prueba unitaria directa de _recompute_live_risk_counters con el
    parámetro pause_lifted_at, sin pasar por el log en disco."""

    def test_cutoff_of_zero_behaves_like_before(self):
        trades = [
            auto_executor.LiveTrade(
                trade_id=f"LIVE-{i}", position_id=f"P{i}", order_id="O",
                timestamp=0, symbol="X", direction="buy", amount_usd=10,
                entry_price=1, stop_loss=1, take_profit=1, proposal_id="p",
                status="closed_loss", pnl_usd=-1.0, pnl_source="real", closed_at=float(i),
            )
            for i in range(1, 4)
        ]
        daily, consecutive = auto_executor._recompute_live_risk_counters(trades, pause_lifted_at=0.0)
        assert consecutive == 3

    def test_cutoff_excludes_closes_at_or_before_it(self):
        trades = [
            auto_executor.LiveTrade(
                trade_id=f"LIVE-{i}", position_id=f"P{i}", order_id="O",
                timestamp=0, symbol="X", direction="buy", amount_usd=10,
                entry_price=1, stop_loss=1, take_profit=1, proposal_id="p",
                status="closed_loss", pnl_usd=-1.0, pnl_source="real", closed_at=float(i),
            )
            for i in range(1, 4)  # closed_at = 1.0, 2.0, 3.0
        ]
        # Corte exactamente en 2.0 -- esa y todo lo anterior quedan fuera.
        daily, consecutive = auto_executor._recompute_live_risk_counters(trades, pause_lifted_at=2.0)
        assert consecutive == 1  # solo el cierre en 3.0 cuenta


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
