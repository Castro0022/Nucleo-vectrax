"""
tests/test_etoro_auto_live_promotion.py — Promoción automática PAPER → LIVE.

Cubre el comportamiento agregado a connectors/etoro/auto_executor.py para que
Vectrax pase de PAPER a LIVE en el instante en que se cierra la operación
PAPER que alcanza `min_paper_signals` (por defecto 30), sin que el creador
tenga que ejecutar /vx market live on en ese momento.

Reglas verificadas aquí (acordadas explícitamente con el creador, incluida la
corrección del 2026-09-23 sobre la revisión del PR #130):
  - Gate ÚNICO, compartido por el automático (cierre de la PAPER #30) y por
    /vx market live on: ≥30 operaciones PAPER cerradas + entorno real
    (ETORO_ENVIRONMENT=real) + credenciales reales. NINGUNO de los dos exige
    win rate — `min_paper_win_rate` sigue en la config y se reporta como dato
    informativo, pero no bloquea ninguna vía.
  - Si falta una condición, el modo permanece en PAPER y el motivo queda
    visible en cfg["last_auto_promotion_reason"] / format_auto_status().
  - Es un disparo único (one-shot): solo promueve la primera vez que se sale
    de la fase PAPER inicial. Si un corte de seguridad (pérdidas consecutivas
    o pérdida diaria) revierte LIVE a PAPER, una operación PAPER posterior NO
    vuelve a promover sola — hace falta el comando manual.
  - record_trade_result(is_paper=True) EXIGE un trade_id y lo usa como clave
    de idempotencia: un mismo trade_id reenviado (p. ej. dos corridas
    solapadas de check_open_positions() sobre la misma operación aún "open")
    no vuelve a sumar a paper_trades_total ni reevalúa la promoción. Esa
    idempotencia es ATÓMICA de verdad: dos procesos del SO realmente
    simultáneos (el modelo real de producción — 4 procesos bajo supervisor,
    ver Dockerfile) cerrando la MISMA operación cuentan exactamente una vez,
    verificado con multiprocessing.Process + Barrier, no solo con llamadas
    secuenciales.
  - Nunca escribe ETORO_ENVIRONMENT ni coloca una orden real, ni siquiera
    cuando esta suite corre.
  - _load_config() nunca devuelve un contenedor mutable (approved_symbols,
    daily_ops_by_symbol, recorded_paper_trade_ids) que sea EL MISMO objeto
    que el de auto_executor.DEFAULTS — esa era la causa raíz real de por
    qué las pruebas nuevas pasaban solas pero fallaban junto al resto de
    la suite: cualquier prueba (de este archivo o de cualquier otro) que
    llegara primero al camino "sin config todavía" mutaba DEFAULTS mismo,
    y esa contaminación quedaba para el resto del proceso de pytest.
"""
from __future__ import annotations

import ast
import itertools
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


_trade_seq = itertools.count(1)


def _new_trade_id() -> str:
    return f"PAPER-TEST-{next(_trade_seq)}"


def _close_paper(pnl: float, n: int = 1) -> list[str]:
    """Close `n` DISTINCT paper trades (each its own trade_id) and return the
    ids used, in order — so a test can replay the last one to simulate a
    duplicate/retried close of that exact operation."""
    ids = []
    for _ in range(n):
        tid = _new_trade_id()
        auto_executor.record_trade_result(pnl, is_paper=True, trade_id=tid)
        ids.append(tid)
    return ids


def _mp_close_worker(
    config_file: str, trade_id: str, pnl: float, barrier, result_queue,
    widen_race: bool = False,
) -> None:
    """multiprocessing.Process target — must be module-level to be
    picklable under the "spawn" start method (used with "fork" here, but
    kept portable). Runs in a SEPARATE OS PROCESS, mirroring the real
    production model: 4 separate processes under supervisor (Telegram
    Gateway, Pipeline Worker, Core API, Meta Loop — see Dockerfile), where
    e.g. the Pipeline Worker's periodic learning cycle and a
    creator-triggered /vx etoro learn run from the Telegram Gateway can
    both reach record_trade_result() around the same time. The barrier
    holds every worker at the door and releases them together, so they
    call record_trade_result() at essentially the same instant — the
    actual race window a threading.Lock cannot close (it doesn't reach
    across processes), and only an inter-process mechanism (here,
    _locked_config()'s fcntl.flock()) can.

    Reports back through `result_queue` whether THIS call logged the
    "duplicado" warning (i.e. found trade_id already recorded). This is
    the oracle that actually proves mutual exclusion — checking only the
    final paper_trades_total is NOT enough when both workers use the same
    trade_id and pnl: an UNSYNCHRONIZED lost-update race (both read
    total=0 before either writes, both independently compute 0+1=1, the
    second write just overwrites the first with the same number) lands on
    the identical final total=1 as the correct, properly-serialized
    outcome, by coincidence — so a naive "total == 1" assertion cannot
    tell a real fix from a race that got lucky. Whether exactly one call
    saw the other's committed write (and logged the duplicate warning)
    can. `widen_race=True` adds a deliberate delay right after the
    protected load, inside the critical section, to make two forked
    processes released together overlap even on a fast machine where they
    might otherwise happen to run back-to-back — it does not change what
    is being tested, only makes the interleaving attempt reliable."""
    import logging
    import connectors.etoro.auto_executor as ae
    ae._CONFIG_FILE = config_file

    if widen_race:
        _orig_load_config = ae._load_config

        def _slow_load_config():
            cfg = _orig_load_config()
            import time as _t
            _t.sleep(0.1)
            return cfg
        ae._load_config = _slow_load_config

    saw_duplicate = {"flag": False}

    class _DuplicateCapture(logging.Handler):
        def emit(self, record):
            if "duplicado" in record.getMessage():
                saw_duplicate["flag"] = True

    logging.getLogger("vectrax.etoro.auto_executor").addHandler(_DuplicateCapture())

    try:
        barrier.wait(timeout=10)
    except Exception:
        pass  # proceed anyway — a missed rendezvous still exercises the lock
    ae.record_trade_result(pnl, is_paper=True, trade_id=trade_id)
    result_queue.put(saw_duplicate["flag"])


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
        # 20 losses + 9 wins = 29 closes, WR far below 60% — no gate cares.
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


# ── 2. Caso con menos de 60% — ningún gate lo exige, ni el automático ni el
#      manual (corrección del creador sobre la primera versión del PR) ────

class TestWinRateIsNeverAGate:

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

    def test_live_readiness_reasons_never_mention_win_rate(self, isolated_executor, real_env):
        cfg = auto_executor.get_config()
        cfg["paper_trades_total"] = 30
        cfg["paper_trades_wins"] = 0   # WR = 0%
        reasons = auto_executor._live_readiness_reasons(cfg)
        assert reasons == []
        assert not any("win rate" in r.lower() or " wr" in r.lower() for r in reasons)

    def test_manual_activate_live_also_ignores_win_rate(self, isolated_executor, real_env):
        """The creator's correction: /vx market live on must NOT require 60%
        either — only the automatic path was exempted in the first version
        of this PR; the manual command still blocked below 60%. Fixed."""
        cfg = auto_executor.get_config()
        cfg["mode"] = AutoMode.PAPER.value
        cfg["paper_trades_total"] = 30
        cfg["paper_trades_wins"] = 1   # WR ≈ 3.3% — would fail the old 60% gate
        auto_executor._save_config(cfg)

        result = auto_executor.activate_live("tg:2030762343")
        assert "🔴" in result
        assert "❌" not in result
        assert auto_executor.get_mode() == AutoMode.LIVE

    def test_status_panel_shows_win_rate_as_informational_not_a_gate(
        self, isolated_executor, real_env
    ):
        cfg = auto_executor.get_config()
        cfg["paper_trades_total"] = 5
        cfg["paper_trades_wins"] = 0
        auto_executor._save_config(cfg)
        panel = auto_executor.format_auto_status()
        assert "informativo" in panel.lower()
        assert "no es requisito" in panel.lower()


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
        cfg["paper_trades_wins"] = 5
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


# ── 5. Resultados duplicados — protección REAL del contador ──────────────

class TestDuplicateResultsDoNotInflateTheCounter:

    def test_missing_trade_id_for_paper_result_is_rejected(self, isolated_executor, real_env):
        """The idempotency key is not optional: a caller that cannot name
        the operation must not be able to advance the PAPER counter."""
        with pytest.raises(ValueError):
            auto_executor.record_trade_result(1.0, is_paper=True)
        assert auto_executor.get_config()["paper_trades_total"] == 0

    def test_duplicate_trade_id_at_the_threshold_does_not_inflate_the_count(
        self, isolated_executor, real_env, tg_calls
    ):
        """
        The exact race Mario described: check_open_positions() runs twice,
        overlapping, on the same still-"open" 30th trade — both calls reach
        record_trade_result() with the SAME trade_id. paper_trades_total
        must end at 30, not 31, and the promotion must not double-fire.
        """
        auto_executor.activate_paper()
        _close_paper(1.0, n=29)
        assert auto_executor.get_config()["paper_trades_total"] == 29

        thirtieth_id = _new_trade_id()
        auto_executor.record_trade_result(1.0, is_paper=True, trade_id=thirtieth_id)
        assert auto_executor.get_config()["paper_trades_total"] == 30
        assert auto_executor.get_mode() == AutoMode.LIVE
        assert len(tg_calls) == 1
        activated_at = auto_executor.get_config()["live_activated_at"]

        # The retried/duplicated delivery of the SAME close event.
        auto_executor.record_trade_result(1.0, is_paper=True, trade_id=thirtieth_id)
        cfg = auto_executor.get_config()
        assert cfg["paper_trades_total"] == 30, "a duplicate result must NOT inflate the counter"
        assert cfg["paper_trades_wins"] == 30
        assert cfg["mode"] == AutoMode.LIVE.value
        assert cfg["live_activated_at"] == activated_at   # untouched
        assert len(tg_calls) == 1                          # not re-notified

    def test_duplicate_trade_id_before_threshold_does_not_advance_the_count(
        self, isolated_executor, real_env, tg_calls
    ):
        """A retry storm on the 29th operation (recorded 3 times under the
        same trade_id) must still count as exactly one — it must never be
        the thing that silently pushes the total to 30/30."""
        auto_executor.activate_paper()
        _close_paper(1.0, n=28)
        assert auto_executor.get_config()["paper_trades_total"] == 28

        retry_id = _new_trade_id()
        for _ in range(3):
            auto_executor.record_trade_result(1.0, is_paper=True, trade_id=retry_id)

        cfg = auto_executor.get_config()
        assert cfg["paper_trades_total"] == 29, "3 retries of the same close must count once"
        assert auto_executor.get_mode() == AutoMode.PAPER   # still short of 30
        assert tg_calls == []

    def test_duplicate_blocked_evaluation_is_idempotent(
        self, isolated_executor, monkeypatch, tg_calls
    ):
        monkeypatch.setenv("ETORO_ENVIRONMENT", "demo")   # keeps it blocked
        monkeypatch.setenv("ETORO_API_KEY", "k")
        monkeypatch.setenv("ETORO_USER_KEY", "u")
        auto_executor.activate_paper()
        _close_paper(1.0, n=30)
        reason_1 = auto_executor.get_config()["last_auto_promotion_reason"]

        _close_paper(1.0, n=1)   # another (distinct) close, still blocked the same way
        reason_2 = auto_executor.get_config()["last_auto_promotion_reason"]

        assert reason_1 == reason_2
        assert auto_executor.get_mode() == AutoMode.PAPER
        assert tg_calls == []


# ── 5b. Concurrencia REAL entre procesos — no solo llamadas secuenciales ──

class TestConcurrentIdempotencyAcrossProcesses:
    """
    The tests above call record_trade_result() sequentially, one call
    fully returning before the next starts — they prove the trade_id
    dedup logic is correct, but NOT that it is safe against two callers
    genuinely overlapping in time. Production runs 4 separate OS
    processes under supervisor (Telegram Gateway, Pipeline Worker, Core
    API, Meta Loop — see Dockerfile): the Pipeline Worker's periodic
    30-minute learning cycle and a creator-triggered /vx etoro learn run
    handled by the Telegram Gateway can both reach
    check_open_positions() -> record_trade_result() around the same
    moment, from DIFFERENT processes. These tests force that with real
    multiprocessing.Process workers released together by a
    multiprocessing.Barrier, so both actually call record_trade_result()
    at essentially the same instant — the only way to exercise
    _locked_config()'s fcntl.flock() the way it is actually exercised in
    production (a threading.Lock would never even be threatened by two
    separate processes, so a thread-only test would not prove anything
    here).
    """

    def test_two_simultaneous_processes_count_the_same_trade_id_exactly_once(
        self, isolated_executor, real_env, tg_calls
    ):
        """
        The real oracle here is NOT just the final total (see
        _mp_close_worker's docstring for why total==1 alone can be a
        coincidence, not proof): exactly ONE of the two processes must
        have found the trade_id already recorded and logged the
        "duplicado" warning. If mutual exclusion actually held, one
        process's full read-modify-write happens entirely before the
        other's; if it did not, both could read the not-yet-recorded
        state and neither would ever see a duplicate — even though the
        final total might still accidentally read 1.
        """
        import multiprocessing

        auto_executor.activate_paper()
        config_file = auto_executor._CONFIG_FILE
        trade_id = "RACE-SAME-OPERATION"

        ctx = multiprocessing.get_context("fork")
        barrier = ctx.Barrier(2)
        q = ctx.Queue()
        procs = [
            ctx.Process(
                target=_mp_close_worker,
                args=(config_file, trade_id, 1.0, barrier, q, True),
            )
            for _ in range(2)
        ]
        for p in procs:
            p.start()
        saw_duplicate = sorted(q.get(timeout=15) for _ in procs)
        for p in procs:
            p.join(timeout=15)
            assert p.exitcode == 0, f"worker process crashed (exitcode={p.exitcode})"

        assert saw_duplicate == [False, True], (
            "exactly one of the two concurrent processes must detect the "
            "trade_id as already recorded — both False means neither saw "
            "the other's write (a lost-update race despite the lock), "
            "both True is impossible unless neither ever counted it"
        )

        cfg = auto_executor.get_config()
        assert cfg["paper_trades_total"] == 1, (
            "two genuinely concurrent processes closing the SAME trade_id "
            "must count it exactly once, not once per process"
        )
        assert cfg["paper_trades_wins"] == 1
        assert cfg["recorded_paper_trade_ids"].count(trade_id) == 1

    def test_two_simultaneous_processes_with_different_trade_ids_both_count(
        self, isolated_executor, real_env, tg_calls
    ):
        """Guards against an over-broad fix (e.g. a lock held so long, or
        scoped so wide, that it drops or merges legitimate distinct closes
        that happen to race) — two DIFFERENT real operations closing at
        the same instant must both be counted, and neither should have
        logged a spurious duplicate warning."""
        import multiprocessing

        auto_executor.activate_paper()
        config_file = auto_executor._CONFIG_FILE

        ctx = multiprocessing.get_context("fork")
        barrier = ctx.Barrier(2)
        q = ctx.Queue()
        procs = [
            ctx.Process(
                target=_mp_close_worker,
                args=(config_file, f"RACE-DISTINCT-{i}", 1.0, barrier, q, True),
            )
            for i in range(2)
        ]
        for p in procs:
            p.start()
        saw_duplicate = [q.get(timeout=15) for _ in procs]
        for p in procs:
            p.join(timeout=15)
            assert p.exitcode == 0, f"worker process crashed (exitcode={p.exitcode})"

        assert saw_duplicate == [False, False]

        cfg = auto_executor.get_config()
        assert cfg["paper_trades_total"] == 2
        assert cfg["paper_trades_wins"] == 2


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


# ── 7. Causa raíz de "pasa solo, falla junto al resto": DEFAULTS mutable ──

class TestConfigIsolationFromGlobalDefaults:
    """
    The actual reason 14 of these tests failed only when run inside the
    full 4.752-test suite (reported on PR #130): _load_config() used to
    return dict(DEFAULTS), a SHALLOW copy. Any DEFAULTS value that is a
    list/dict (approved_symbols, daily_ops_by_symbol, and this PR's new
    recorded_paper_trade_ids) was therefore the SAME object as
    auto_executor.DEFAULTS[...] whenever no config file existed yet — the
    exact situation every isolated_executor-based test starts from. The
    first test (anywhere in the whole suite, not just this file) to mutate
    that list in place — record_trade_result()'s dedup list included —
    permanently corrupted the shared DEFAULTS global for every test that
    ran afterward in the same pytest process, in whatever order the full
    suite happened to collect them. Standalone runs of just this file
    never exposed it reliably because ordering differs. Fixed by
    _load_config() returning copy.deepcopy(DEFAULTS) instead.
    """

    def test_load_config_never_aliases_a_mutable_default(self, isolated_executor):
        cfg = auto_executor.get_config()
        for key in ("approved_symbols", "daily_ops_by_symbol", "recorded_paper_trade_ids"):
            assert cfg[key] is not auto_executor.DEFAULTS[key], (
                f"cfg[{key!r}] is the SAME object as DEFAULTS[{key!r}] — "
                "mutating it would corrupt every other test in this process"
            )

    def test_mutating_returned_config_never_leaks_into_defaults_or_other_tests(
        self, isolated_executor, real_env, tg_calls
    ):
        # Exactly the sequence that corrupted DEFAULTS before the fix: the
        # very first record_trade_result() call in a brand-new isolated
        # config (no file on disk yet).
        auto_executor.activate_paper()
        auto_executor.record_trade_result(1.0, is_paper=True, trade_id="ISOLATION-CHECK")

        assert auto_executor.DEFAULTS["recorded_paper_trade_ids"] == []
        assert auto_executor.DEFAULTS["approved_symbols"] == []
        assert auto_executor.DEFAULTS["daily_ops_by_symbol"] == {}

    def test_a_second_unrelated_isolated_config_starts_clean(
        self, isolated_executor, real_env, tg_calls, tmp_path, monkeypatch
    ):
        """Simulates two DIFFERENT tests (this one running after the one
        above, sharing the same Python process like the real suite does):
        a second, wholly unrelated isolated config must start with an
        empty dedup list, not one poisoned by the previous test."""
        auto_executor.activate_paper()
        auto_executor.record_trade_result(1.0, is_paper=True, trade_id="FIRST-TEST-TRADE")

        # A second, independent isolated config — as if a different test
        # function (fresh tmp_path) ran next in the same process.
        monkeypatch.setattr(auto_executor, "_CONFIG_FILE", str(tmp_path / "other_cfg.json"))
        monkeypatch.setattr(auto_executor, "_PAPER_LOG_FILE", str(tmp_path / "other_trades.jsonl"))
        auto_executor.activate_paper()
        cfg2 = auto_executor.get_config()
        assert cfg2["recorded_paper_trade_ids"] == []
        assert cfg2["paper_trades_total"] == 0


# ── 8. Nunca toca ETORO_ENVIRONMENT ni coloca una orden real ─────────────

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
