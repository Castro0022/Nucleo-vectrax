"""
Tests for core.continuity_reentry — máquina de estados de 3 nudges.

Tests obligatorios:
  1. nudge #1 respeta ventana 12-20h
  2. nudge #2 respeta espera de 3-5 días tras nudge #1
  3. nudge #3 respeta espera de varias semanas (21-30d), máx 1/mes
  4. después de nudge #3 sin respuesta → DORMANT, sin nudge #4 jamás
  5. DORMANT se reactiva solo con mensaje entrante del usuario
  6. los 3 mensajes son textualmente distintos entre sí (distintas instrucciones de tono)
  7. ventana horaria 09:00-21:00 se respeta en los 3 nudges
"""
from __future__ import annotations

import sqlite3
import time

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets its own in-memory-path DB so they never share state."""
    db_file = str(tmp_path / "continuity_reentry.db")
    import core.continuity_reentry as cr
    monkeypatch.setattr(cr, "_DB_PATH", db_file)
    yield db_file


@pytest.fixture()
def stub_llm(monkeypatch):
    """Return distinct LLM-like responses per nudge number by reading the prompt."""
    _responses = {
        1: "Aquí estoy cuando quieras empezar.",
        2: "Hace días que no hablamos, sigo disponible.",
        3: "Sigo aquí en silencio, escríbeme cuando lo necesites.",
    }

    def _fake_route(prompt: str) -> dict:
        for n, text in _responses.items():
            if f"nudge #{n}" in prompt:
                return {"success": True, "content": text}
        return {"success": True, "content": "Mensaje genérico."}

    import vectrax.intelligence_bridge as ib
    monkeypatch.setattr(ib, "is_ready", lambda: True, raising=False)
    monkeypatch.setattr(ib, "route_single", _fake_route, raising=False)
    return _responses


def _insert_state(db_path, user_id, nudge_count, next_nudge_after,
                  status="active", last_activity=None):
    now = last_activity or time.time()
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reentry_state (
            user_id             TEXT PRIMARY KEY,
            first_seen          REAL NOT NULL DEFAULT 0,
            last_activity       REAL NOT NULL DEFAULT 0,
            nudge_count         INTEGER NOT NULL DEFAULT 0,
            last_nudge_at       REAL NOT NULL DEFAULT 0,
            next_nudge_after    REAL NOT NULL DEFAULT 0,
            status              TEXT NOT NULL DEFAULT 'active',
            reentry_after_hours REAL NOT NULL DEFAULT 0,
            reentry_sent        INTEGER NOT NULL DEFAULT 0,
            reentry_sent_at     REAL NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        INSERT OR REPLACE INTO reentry_state
            (user_id, first_seen, last_activity, nudge_count,
             last_nudge_at, next_nudge_after, status)
        VALUES (?, ?, ?, ?, 0, ?, ?)
    """, (user_id, now, now, nudge_count, next_nudge_after, status))
    conn.commit()
    conn.close()


def _read_state(db_path, user_id):
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT nudge_count, status, next_nudge_after FROM reentry_state WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return {"nudge_count": row[0], "status": row[1], "next_nudge_after": row[2]} if row else None


def _midday_ts() -> float:
    """Return a timestamp corresponding to 12:00 local time today."""
    import datetime
    now = datetime.datetime.now()
    return now.replace(hour=12, minute=0, second=0, microsecond=0).timestamp()


def _night_ts() -> float:
    """Return a timestamp corresponding to 02:00 local time today."""
    import datetime
    now = datetime.datetime.now()
    return now.replace(hour=2, minute=0, second=0, microsecond=0).timestamp()


# ---------------------------------------------------------------------------
# Test 1 — nudge #1 respects 12-20h silence window
# ---------------------------------------------------------------------------

def test_nudge1_not_sent_before_threshold(isolated_db, stub_llm, monkeypatch):
    """Threshold still in the future → nudge #1 must NOT fire."""
    import core.continuity_reentry as cr
    now = _midday_ts()
    _insert_state(isolated_db, "tg:111", 0, now + 14 * 3600)
    monkeypatch.setattr(time, "time", lambda: now)
    assert cr.check_reentry(lambda cid, txt: True) == 0


def test_nudge1_sent_after_threshold(isolated_db, stub_llm, monkeypatch):
    """Threshold passed → nudge #1 fires and nudge_count becomes 1."""
    import core.continuity_reentry as cr
    now = _midday_ts()
    _insert_state(isolated_db, "tg:111", 0, now - 3600)
    monkeypatch.setattr(time, "time", lambda: now)
    assert cr.check_reentry(lambda cid, txt: True) == 1

    state = _read_state(isolated_db, "tg:111")
    assert state["nudge_count"] == 1
    assert state["status"] == "active"
    assert state["next_nudge_after"] > now + cr.NUDGE2_MIN_DAYS * 86400 - 1


# ---------------------------------------------------------------------------
# Test 2 — nudge #2 respects 3-5 day wait after nudge #1
# ---------------------------------------------------------------------------

def test_nudge2_not_sent_before_3_days(isolated_db, stub_llm, monkeypatch):
    """Threshold in the future → nudge #2 must NOT fire."""
    import core.continuity_reentry as cr
    now = _midday_ts()
    _insert_state(isolated_db, "tg:222", 1, now + 86400)
    monkeypatch.setattr(time, "time", lambda: now)
    assert cr.check_reentry(lambda cid, txt: True) == 0


def test_nudge2_sent_after_3_days(isolated_db, stub_llm, monkeypatch):
    """3+ days elapsed → nudge #2 fires and nudge_count becomes 2."""
    import core.continuity_reentry as cr
    now = _midday_ts()
    _insert_state(isolated_db, "tg:222", 1, now - 3600)
    monkeypatch.setattr(time, "time", lambda: now)
    assert cr.check_reentry(lambda cid, txt: True) == 1

    state = _read_state(isolated_db, "tg:222")
    assert state["nudge_count"] == 2
    assert state["status"] == "active"
    assert state["next_nudge_after"] > now + cr.NUDGE3_MIN_DAYS * 86400 - 1


# ---------------------------------------------------------------------------
# Test 3 — nudge #3 respects several weeks (21-30 days, max 1/month)
# ---------------------------------------------------------------------------

def test_nudge3_not_sent_before_3_weeks(isolated_db, stub_llm, monkeypatch):
    """Only 10 days since nudge #2 → nudge #3 must NOT fire."""
    import core.continuity_reentry as cr
    now = _midday_ts()
    _insert_state(isolated_db, "tg:333", 2, now + 11 * 86400)
    monkeypatch.setattr(time, "time", lambda: now)
    assert cr.check_reentry(lambda cid, txt: True) == 0


def test_nudge3_sent_after_3_weeks(isolated_db, stub_llm, monkeypatch):
    """21+ days elapsed → nudge #3 fires and user goes DORMANT."""
    import core.continuity_reentry as cr
    now = _midday_ts()
    _insert_state(isolated_db, "tg:333", 2, now - 3600)
    monkeypatch.setattr(time, "time", lambda: now)
    assert cr.check_reentry(lambda cid, txt: True) == 1

    state = _read_state(isolated_db, "tg:333")
    assert state["nudge_count"] == 3
    assert state["status"] == "dormant"
    assert state["next_nudge_after"] == 0


# ---------------------------------------------------------------------------
# Test 4 — DORMANT after nudge #3 → no nudge #4, ever
# ---------------------------------------------------------------------------

def test_no_nudge4_for_dormant_user(isolated_db, stub_llm, monkeypatch):
    """User already DORMANT → check_reentry never sends anything."""
    import core.continuity_reentry as cr
    now = _midday_ts()
    _insert_state(isolated_db, "tg:444", 3, now - 3600, status="dormant")
    monkeypatch.setattr(time, "time", lambda: now)
    assert cr.check_reentry(lambda cid, txt: True) == 0

    state = _read_state(isolated_db, "tg:444")
    assert state["status"] == "dormant"
    assert state["nudge_count"] == 3


def test_active_with_overflowed_nudge_count_goes_dormant_without_send(
    isolated_db, stub_llm, monkeypatch
):
    """Safety guard: nudge_count > 3 → mark DORMANT, never send."""
    import core.continuity_reentry as cr
    now = _midday_ts()
    _insert_state(isolated_db, "tg:445", 4, now - 3600, status="active")
    captured = []
    monkeypatch.setattr(time, "time", lambda: now)
    cr.check_reentry(lambda cid, txt: captured.append(txt) or True)
    assert len(captured) == 0
    assert _read_state(isolated_db, "tg:445")["status"] == "dormant"


# ---------------------------------------------------------------------------
# Test 5 — DORMANT reactivates only on incoming user message
# ---------------------------------------------------------------------------

def test_dormant_reactivates_on_record_activity(isolated_db, monkeypatch):
    """record_activity() on DORMANT user resets nudge_count and restores active."""
    import core.continuity_reentry as cr
    now = _midday_ts()
    _insert_state(isolated_db, "tg:555", 3, 0, status="dormant")
    monkeypatch.setattr(time, "time", lambda: now)
    cr.record_activity("tg:555")

    state = _read_state(isolated_db, "tg:555")
    assert state["nudge_count"] == 0
    assert state["status"] == "active"
    assert state["next_nudge_after"] > now


def test_dormant_stays_dormant_without_activity(isolated_db, stub_llm, monkeypatch):
    """DORMANT user without incoming message stays DORMANT after check_reentry."""
    import core.continuity_reentry as cr
    now = _midday_ts()
    _insert_state(isolated_db, "tg:556", 3, 0, status="dormant")
    monkeypatch.setattr(time, "time", lambda: now)
    cr.check_reentry(lambda cid, txt: True)
    assert _read_state(isolated_db, "tg:556")["status"] == "dormant"


# ---------------------------------------------------------------------------
# Test 6 — The 3 tone directives are textually distinct
# ---------------------------------------------------------------------------

def test_three_nudge_tones_are_textually_distinct():
    """_TONE_DIRECTIVES must have 3 entries with mutually distinct content."""
    from core.continuity_reentry import _TONE_DIRECTIVES

    assert set(_TONE_DIRECTIVES.keys()) == {1, 2, 3}
    t1, t2, t3 = _TONE_DIRECTIVES[1], _TONE_DIRECTIVES[2], _TONE_DIRECTIVES[3]
    assert len(t1) > 20 and len(t2) > 20 and len(t3) > 20
    assert t1 != t2 and t2 != t3 and t1 != t3


def test_nudge_prompts_embed_correct_tone_per_number(isolated_db, monkeypatch):
    """LLM prompt for each nudge must contain the matching tone directive."""
    from core.continuity_reentry import _TONE_DIRECTIVES, _build_reentry_message

    captured: list[str] = []
    import vectrax.intelligence_bridge as ib
    monkeypatch.setattr(ib, "is_ready", lambda: True, raising=False)
    monkeypatch.setattr(
        ib, "route_single",
        lambda p: captured.append(p) or {"success": True, "content": "ok"},
        raising=False,
    )

    for n in (1, 2, 3):
        captured.clear()
        _build_reentry_message("tg:999", nudge_number=n)
        assert len(captured) == 1
        assert f"nudge #{n}" in captured[0]
        assert _TONE_DIRECTIVES[n][:40] in captured[0]


# ---------------------------------------------------------------------------
# Test 7 — Time window 09:00-21:00 respected in all nudges
# ---------------------------------------------------------------------------

def test_no_nudge_outside_send_window(isolated_db, stub_llm, monkeypatch):
    """At 02:00 no nudge fires regardless of state."""
    import core.continuity_reentry as cr
    night = _night_ts()
    for uid in ("tg:701", "tg:702", "tg:703"):
        n = int(uid.split(":")[1]) - 700
        _insert_state(isolated_db, uid, n - 1, night - 3600)
    monkeypatch.setattr(time, "time", lambda: night)
    assert cr.check_reentry(lambda cid, txt: True) == 0


def test_nudge_fires_at_midday(isolated_db, stub_llm, monkeypatch):
    """At 12:00 a due nudge is sent."""
    import core.continuity_reentry as cr
    midday = _midday_ts()
    _insert_state(isolated_db, "tg:888", 0, midday - 3600)
    monkeypatch.setattr(time, "time", lambda: midday)
    assert cr.check_reentry(lambda cid, txt: True) == 1


def test_send_window_boundaries():
    """_in_send_window returns False before 09:00 and at/after 21:00."""
    import datetime
    from core.continuity_reentry import _in_send_window

    def _ts(hour):
        d = datetime.datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)
        return d.timestamp()

    assert _in_send_window(_ts(8)) is False
    assert _in_send_window(_ts(9)) is True
    assert _in_send_window(_ts(14)) is True
    assert _in_send_window(_ts(20)) is True
    assert _in_send_window(_ts(21)) is False
    assert _in_send_window(_ts(23)) is False
