"""
tests/test_etoro_auto_live_promotion.py — Promoción automática PAPER → LIVE.

Cubre el comportamiento agregado a connectors/etoro/auto_executor.py para que
Vectrax pase de PAPER a LIVE en el instante en que se cierra la operación
PAPER que alcanza `min_paper_signals` (por defecto 30), sin que el creador
tenga que ejecutar /vx market live on en ese momento.

Reglas verificadas aquí (acordadas explícitamente con el creador):
  - El gate AUTOMÁTICO exige: ≥30 operaciones PAPER cerradas, entorno real
    (ETORO_ENVIRONMENT=real) y credenciales reales configuradas. Deliberadamente
    NO exige win rate.
  - El gate MANUAL (/vx market live on, activate_live()) es el que conserva el
    requisito de ≥60% de aciertos — sin cambios respecto al comportamiento
    previo.
  - Si falta una condición del gate automático, el modo permanece en PAPER y
    el motivo queda visible en cfg["last_auto_promotion_reason"] /
    format_auto_status().
  - Es un disparo único (one-shot): solo promueve la primera vez que se sale
    de la fase PAPER inicial. Si un corte de seguridad (pérdidas consecutivas
    o pérdida diaria) revierte LIVE a PAPER, una operación PAPER posterior NO
    vuelve a promover sola — hace falta el comando manual.
  - Nunca escribe ETORO_ENVIRONMENT ni coloca una orden real, ni siquiera
    cuando esta suite corre.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from connectors.etoro import auto_executor
from connectors.etoro.auto_executor import AutoMode

_AUTO_EXECUTOR_FILE = _ROOT / "connectors" / "etoro" / "auto_executor.py"


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture()
def isolated_executor(tmp_path, monkeypatch):
    """Point the auto-executor config/paper-log at fresh temp files."""
    monkeypatch.setattr(auto_executor, "_CONFIG_FILE", str(tmp_path / "auto_cfg.json"))
    monkeypatch.setattr(auto_executor, "_PAPER_LOG_FILE", str(tmp_path / "paper_trades.jsonl"))
    return tmp_path


@pytest.fixture()
def real_env(monkeypatch):
    """Simulate the creator having already set a real environment + real
    credentials (never written by our code — only read)."""
    monkeypatch.setenv("ETORO_ENVIRONMENT", "real")
    monkeypatch.setenv("ETORO_API_KEY", "test-real-api-key")
    monkeypatch.setenv("ETORO_USER_KEY", "test-real-user-key")


@pytest.fixture(autouse=True)
def tg_calls(monkeypatch):
    """Capture Telegram notifications instead of ever touching the network."""
    from connectors.etoro import learning_engine
    sent = []
    monkeypatch.setattr(
        learning_engine, "_tg_notify", lambda text: sent.append(text) or True
    )
    return sent


@pytest.fixture(autouse=True)
def no_real_orders(monkeypatch):
    """Poison pill: any attempt to place a real order fails the test loudly."""
    def _boom(*a, **k):
        raise AssertionError(
            "a real order must never be placed while testing the auto-promotion gate"
        )
    from connectors.etoro import trade_executor
    monkeypatch.setattr(trade_executor, "execute_open", _boom)


def _close_paper(pnl: float, n: int = 1) -> None:
    for _ in range(n):
        auto_executor.record_trade_result(pnl, is_paper=True)


# ── 1. Transición 29 → 30 ────────────────────────────────────────────────

class TestTransition29To30:

    def test_29_closes_stay_in_paper(self, isolated_executor, real_env, tg_calls):
        auto_executor.activate_paper()
        _close_paper(1.0, n=29)

        cfg = auto_executor.get_config()
        assert cfg["paper_trades_total"] == 29
        assert auto_executor.get_mode() == AutoMode.PAPER
        assert tg_calls == []

    def test_30th_close_promotes_to_live_regardless_of_win_rate(
        self, isolated_executor, real_env, tg_calls
    ):
        auto_executor.activate_paper()
        # 20 losses + 9 wins = 29 closes, WR far below 60% — the automatic
        # gate must not care.
        _close_paper(-5.0, n=20)
        _close_paper(5.0, n=9)
        assert auto_executor.get_mode() == AutoMode.PAPER

        # 30th close (also a loss) crosses the threshold.
        _close_paper(-5.0, n=1)

        cfg = auto_executor.get_config()
        assert cfg["paper_trades_total"] == 30
        wr = auto_executor._get_paper_win_rate(cfg)
        assert wr < 60.0, "this test only proves anything if WR is genuinely low"
        assert auto_executor.get_mode() == AutoMode.LIVE
        assert cfg["live_activated_by"] == "auto:paper_threshold"
        assert cfg["live_activated_at"] > 0
        assert cfg["last_auto_promotion_reason"] == ""
        assert len(tg_calls) == 1
        assert "LIVE" in tg_calls[0]


# ── 2. Caso con menos de 60% (el gate automático no lo bloquea) ───────────

class TestWinRateIsNotAnAutomaticGate:

    def test_low_win_rate_does_not_block_automatic_promotion(
        self, isolated_executor, real_env, tg_calls
    ):
        auto_executor.activate_paper()
        _close_paper(-1.0, n=25)   # 25 losses
        _close_paper(1.0, n=5)     # 5 wins -> WR = 16.7%

        cfg = auto_executor.get_config()
        assert cfg["paper_trades_total"] == 30
        assert auto_executor._get_paper_win_rate(cfg) == pytest.approx(16.7, abs=0.1)
        assert auto_executor.get_mode() == AutoMode.LIVE
        assert len(tg_calls) == 1

    def test_auto_promotion_reasons_never_mention_win_rate(self, isolated_executor, real_env):
        cfg = auto_executor.get_config()
        cfg["paper_trades_total"] = 30
        cfg["paper_trades_wins"] = 0
        reasons = auto_executor._auto_promotion_reasons(cfg)
        assert reasons == []
        assert not any("win rate" in r.lower() or "wr" in r.lower() for r in reasons)

    def test_manual_gate_still_requires_60_percent(self, isolated_executor, real_env):
        cfg = auto_executor.get_config()
        cfg["paper_trades_total"] = 30
        cfg["paper_trades_wins"] = 10   # WR = 33%
        reasons = auto_executor._live_readiness_reasons(cfg)
        assert any("Win rate" in r for r in reasons)

    def test_manual_activate_live_blocked_below_60_percent(self, isolated_executor, real_env):
        cfg = auto_executor.get_config()
        cfg["mode"] = AutoMode.PAPER.value
        cfg["paper_trades_total"] = 30
        cfg["paper_trades_wins"] = 10
        auto_executor._save_config(cfg)

        result = auto_executor.activate_live("tg:2030762343")
        assert "❌" in result
        assert "Win rate" in result
        assert auto_executor.get_mode() == AutoMode.PAPER

    def test_manual_activate_live_succeeds_at_60_percent(self, isolated_executor, real_env):
        cfg = auto_executor.get_config()
        cfg["mode"] = AutoMode.PAPER.value
        cfg["paper_trades_total"] = 30
        cfg["paper_trades_wins"] = 18   # exactly 60%
        auto_executor._save_config(cfg)

        result = auto_executor.activate_live("tg:2030762343")
        assert "🔴" in result
        assert auto_executor.get_mode() == AutoMode.LIVE


# ── 3. Condiciones faltantes: permanece en PAPER con motivo visible ──────

class TestBlockedConditionsStayVisible:

    def test_missing_environment_blocks_and_reason_is_visible(
        self, isolated_executor, monkeypatch, tg_calls
    ):
        monkeypatch.setenv("ETORO_ENVIRONMENT", "demo")
        monkeypatch.setenv("ETORO_API_KEY", "k")
        monkeypatch.setenv("ETORO_USER_KEY", "u")
        auto_executor.activate_paper()
        _close_paper(1.0, n=30)

        assert auto_executor.get_mode() == AutoMode.PAPER
        cfg = auto_executor.get_config()
        assert "ETORO_ENVIRONMENT" in cfg["last_auto_promotion_reason"]
        assert tg_calls == []
        # Visible on the status panel too.
        assert "LIVE automático pendiente" in auto_executor.format_auto_status()

    def test_missing_credentials_blocks_and_reason_is_visible(
        self, isolated_executor, monkeypatch, tg_calls
    ):
        monkeypatch.setenv("ETORO_ENVIRONMENT", "real")
        monkeypatch.delenv("ETORO_API_KEY", raising=False)
        monkeypatch.delenv("ETORO_USER_KEY", raising=False)
        auto_executor.activate_paper()
        _close_paper(1.0, n=30)

        assert auto_executor.get_mode() == AutoMode.PAPER
        cfg = auto_executor.get_config()
        assert "redenciales" in cfg["last_auto_promotion_reason"]
        assert tg_calls == []

    def test_below_threshold_count_blocks_and_reason_is_visible(
        self, isolated_executor, real_env, tg_calls
    ):
        auto_executor.activate_paper()
        _close_paper(1.0, n=15)

        assert auto_executor.get_mode() == AutoMode.PAPER
        cfg = auto_executor.get_config()
        assert "15" in cfg["last_auto_promotion_reason"]
        assert tg_calls == []


# ── 4. Reinicios ──────────────────────────────────────────────────────────

class TestRestarts:

    def test_promotion_persists_across_a_restart(self, isolated_executor, real_env, tg_calls):
        auto_executor.activate_paper()
        _close_paper(1.0, n=30)
        assert auto_executor.get_mode() == AutoMode.LIVE
        activated_at = auto_executor.get_config()["live_activated_at"]

        # "Restart": nothing but re-reading the persisted file from disk.
        assert auto_executor.get_mode() == AutoMode.LIVE
        assert auto_executor.get_config()["live_activated_at"] == activated_at

    def test_counter_already_at_30_at_install_time_does_not_self_trigger_on_restart(
        self, isolated_executor, real_env, tg_calls
    ):
        """
        Simulates deploying this feature onto a config that already carries
        paper_trades_total >= min_paper_signals from BEFORE this code
        existed. A restart (config reload, status check) must never by
        itself promote — only a genuine new PAPER close does.
        """
        auto_executor.activate_paper()
        cfg = auto_executor.get_config()
        cfg["paper_trades_total"] = 35
        cfg["paper_trades_wins"] = 5   # low WR too — irrelevant to the auto gate
        auto_executor._save_config(cfg)

        # "Restart": reload state, read status — no trade closes anywhere.
        assert auto_executor.get_mode() == AutoMode.PAPER
        auto_executor.format_auto_status()
        auto_executor.get_config()
        assert auto_executor.get_mode() == AutoMode.PAPER
        assert tg_calls == []

        # The next GENUINE close is what evaluates (and here satisfies) the gate.
        _close_paper(1.0, n=1)
        assert auto_executor.get_mode() == AutoMode.LIVE
        assert len(tg_calls) == 1


# ── 5. Resultados duplicados ──────────────────────────────────────────────

class TestDuplicateResults:

    def test_duplicate_close_after_promotion_does_not_double_fire(
        self, isolated_executor, real_env, tg_calls
    ):
        auto_executor.activate_paper()
        _close_paper(1.0, n=30)
        assert auto_executor.get_mode() == AutoMode.LIVE
        activated_at = auto_executor.get_config()["live_activated_at"]
        assert len(tg_calls) == 1

        # A duplicate/retried close event for what is conceptually the same
        # trade (e.g. a retried position_manager cycle) must not re-fire.
        _close_paper(1.0, n=1)
        cfg = auto_executor.get_config()
        assert cfg["mode"] == AutoMode.LIVE.value
        assert cfg["live_activated_at"] == activated_at        # untouched
        assert len(tg_calls) == 1                               # not re-notified

    def test_duplicate_blocked_evaluation_is_idempotent(
        self, isolated_executor, monkeypatch, tg_calls
    ):
        monkeypatch.setenv("ETORO_ENVIRONMENT", "demo")   # keeps it blocked
        monkeypatch.setenv("ETORO_API_KEY", "k")
        monkeypatch.setenv("ETORO_USER_KEY", "u")
        auto_executor.activate_paper()
        _close_paper(1.0, n=30)
        reason_1 = auto_executor.get_config()["last_auto_promotion_reason"]

        _close_paper(1.0, n=1)   # another close, still blocked the same way
        reason_2 = auto_executor.get_config()["last_auto_promotion_reason"]

        assert reason_1 == reason_2
        assert auto_executor.get_mode() == AutoMode.PAPER
        assert tg_calls == []


# ── 6. Disparo único tras un corte de seguridad ───────────────────────────

class TestOneShotAfterSafetyShutdown:

    def test_never_repromotes_after_a_consecutive_losses_shutdown(
        self, isolated_executor, real_env, tg_calls
    ):
        auto_executor.activate_paper()
        _close_paper(1.0, n=30)
        assert auto_executor.get_mode() == AutoMode.LIVE
        assert len(tg_calls) == 1

        # A real circuit-breaker: 3 consecutive LIVE losses revert to PAPER.
        # This path (record_trade_result(is_paper=False)) is pre-existing
        # and untouched by this change.
        for _ in range(3):
            auto_executor.record_trade_result(-20.0, is_paper=False)
        assert auto_executor.get_mode() == AutoMode.PAPER
        # Either circuit-breaker may report last (both fire on -$20 losses
        # against the default $10 daily-loss limit) — what matters here is
        # that a real safety shutdown actually reverted the mode.
        assert "auto-shutdown" in auto_executor.get_config()["last_shutdown_reason"]

        # New PAPER trades keep closing afterward — count/env/creds are all
        # still trivially satisfied, but the one-shot gate must NOT silently
        # undo the shutdown.
        _close_paper(1.0, n=1)
        assert auto_executor.get_mode() == AutoMode.PAPER
        reason = auto_executor.get_config()["last_auto_promotion_reason"]
        assert "manual" in reason.lower()
        assert len(tg_calls) == 1   # still just the original promotion

        # The creator's manual command still works at any time.
        result = auto_executor.activate_live("tg:2030762343")
        assert "🔴" in result
        assert auto_executor.get_mode() == AutoMode.LIVE


# ── 7. Nunca toca ETORO_ENVIRONMENT ni coloca una orden real ─────────────

class TestStaticSafetyInvariants:

    def test_source_never_assigns_etoro_environment(self):
        src = _AUTO_EXECUTOR_FILE.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AugAssign):
                targets = [node.target]
            for t in targets:
                if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant):
                    if t.slice.value == "ETORO_ENVIRONMENT":
                        pytest.fail(
                            "auto_executor.py must never assign ETORO_ENVIRONMENT"
                        )
        assert 'os.environ["ETORO_ENVIRONMENT"] =' not in src
        assert "os.environ['ETORO_ENVIRONMENT'] =" not in src
        assert "setenv" not in src  # never sets any env var from this module

    def test_promotion_function_never_calls_order_placement(self):
        src = _AUTO_EXECUTOR_FILE.read_text(encoding="utf-8")
        tree = ast.parse(src)
        promote_fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_maybe_auto_promote_to_live"
        )
        called = {
            n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", None)
            for n in ast.walk(promote_fn) if isinstance(n, ast.Call)
        }
        forbidden = {"execute_open", "open_position", "execute_proposal", "close_position"}
        assert not (called & forbidden), (
            f"automatic promotion must never place orders itself: {called & forbidden}"
        )

    def test_full_29_to_30_flow_never_places_a_real_order(
        self, isolated_executor, real_env, tg_calls
    ):
        """End-to-end guard: running the whole promotion flow under test must
        never reach trade_executor.execute_open (poisoned by the
        `no_real_orders` fixture — this would already fail loudly if it did)."""
        auto_executor.activate_paper()
        _close_paper(1.0, n=30)
        assert auto_executor.get_mode() == AutoMode.LIVE
