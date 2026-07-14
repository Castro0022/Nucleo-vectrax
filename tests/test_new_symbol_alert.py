"""
Tests for the new-symbol first-signal alert in learning_engine.

check_new_symbols_first_signal() debe:
  - disparar UNA alerta la primera vez que un símbolo NUEVO genera una señal,
  - ser idempotente (no re-alertar el mismo símbolo),
  - no disparar para símbolos que no están en la lista de nuevos.

Run:  python -m pytest tests/test_new_symbol_alert.py -v
"""
from __future__ import annotations

import os
import sys
import types
from unittest.mock import patch, MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _fake_signal(symbol, direction="sell", status="pending"):
    return types.SimpleNamespace(
        symbol=symbol, direction=direction, status=status, timestamp=1.0,
    )


def test_new_symbol_first_signal_alerts_once(tmp_path):
    from connectors.etoro import learning_engine as le

    flag = str(tmp_path / "alerted.json")
    signals = [_fake_signal("SPY"), _fake_signal("BTC")]

    with patch.object(le, "_NEW_SYM_ALERT_FILE", flag), \
         patch("connectors.etoro.signal_recorder.load_signals", return_value=signals), \
         patch.object(le, "_tg_notify", MagicMock(return_value=True)) as tg, \
         patch("core.self_observation.observation_ledger.record", MagicMock(return_value=1)):
        first = le.check_new_symbols_first_signal()
        assert first == ["SPY"]              # only the NEW symbol
        assert tg.call_count == 1

        # Idempotent: second call must NOT re-alert SPY
        second = le.check_new_symbols_first_signal()
        assert second == []
        assert tg.call_count == 1

    # Flag persisted
    import json
    assert "SPY" in set(json.load(open(flag)))


def test_new_symbol_alert_skips_non_new_symbols(tmp_path):
    from connectors.etoro import learning_engine as le

    flag = str(tmp_path / "alerted2.json")
    # AAPL is an existing (not new) equity symbol → must not alert
    signals = [_fake_signal("AAPL"), _fake_signal("NVDA")]

    with patch.object(le, "_NEW_SYM_ALERT_FILE", flag), \
         patch("connectors.etoro.signal_recorder.load_signals", return_value=signals), \
         patch.object(le, "_tg_notify", MagicMock(return_value=True)) as tg, \
         patch("core.self_observation.observation_ledger.record", MagicMock(return_value=1)):
        res = le.check_new_symbols_first_signal()
        assert res == []
        assert tg.call_count == 0


def test_new_symbol_alert_multiple_symbols(tmp_path):
    from connectors.etoro import learning_engine as le

    flag = str(tmp_path / "alerted3.json")
    signals = [_fake_signal("QQQ"), _fake_signal("META"), _fake_signal("AAPL")]

    with patch.object(le, "_NEW_SYM_ALERT_FILE", flag), \
         patch("connectors.etoro.signal_recorder.load_signals", return_value=signals), \
         patch.object(le, "_tg_notify", MagicMock(return_value=True)) as tg, \
         patch("core.self_observation.observation_ledger.record", MagicMock(return_value=1)):
        res = le.check_new_symbols_first_signal()
        assert res == ["META", "QQQ"]        # sorted, only new symbols
        assert tg.call_count == 2
