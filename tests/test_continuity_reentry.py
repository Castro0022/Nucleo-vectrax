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

import json
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


# ---------------------------------------------------------------------------
# Gravity context tests
# ---------------------------------------------------------------------------

def _make_gravity_db(path: str, user_id: str, records: list[dict]) -> None:
    """Helper: create a minimal deep_memory table with given records."""
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deep_memory (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
            raw_text TEXT NOT NULL, summary TEXT,
            embedding_json TEXT NOT NULL DEFAULT '[]',
            tags_json TEXT, mass REAL DEFAULT 0,
            ts REAL NOT NULL,
            gravity_score REAL DEFAULT 1.0,
            retrieval_count INTEGER DEFAULT 0,
            memory_status TEXT DEFAULT 'ACTIVE'
        )
    """)
    for r in records:
        conn.execute(
            "INSERT INTO deep_memory "
            "(id, user_id, raw_text, summary, embedding_json, tags_json, "
            " mass, ts, gravity_score, retrieval_count, memory_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                r["id"], user_id, r["raw_text"],
                r.get("summary", ""),
                "[]",
                json.dumps(r.get("tags", [])),
                r.get("mass", 5.0),
                r.get("ts", time.time()),
                r.get("gravity_score", 1.0),
                r.get("retrieval_count", 0),
                r.get("memory_status", "ACTIVE"),
            ),
        )
    conn.commit()
    conn.close()


def test_gravity_topics_not_included_when_no_db(monkeypatch):
    """User with no gravity DB — _load_gravity_topics returns [] silently."""
    import core.continuity_reentry as cr
    monkeypatch.setenv("VECTRAX_GRAVITY_DB", "/nonexistent/path/gravity.db")
    topics = cr._load_gravity_topics("tg:111")
    assert topics == []


def test_gravity_topics_high_mass_non_sensitive_included(tmp_path, monkeypatch):
    """High-gravity non-sensitive topic surfaces in the prompt context."""
    import core.continuity_reentry as cr

    db = str(tmp_path / "gravity.db")
    monkeypatch.setenv("VECTRAX_GRAVITY_DB", db)

    _make_gravity_db(db, "tg:222", [
        {"id": "a1", "raw_text": "Proyecto Vectrax lanzamiento Q3",
         "summary": "Lanzamiento Vectrax Q3", "tags": ["negocio"], "mass": 50.0},
        {"id": "a2", "raw_text": "Inversión en BTC",
         "summary": "BTC portfolio", "tags": ["mercado"], "mass": 30.0},
    ])

    topics = cr._load_gravity_topics("tg:222")
    assert len(topics) == 2
    assert any("Vectrax" in t or "vectrax" in t.lower() or "Lanzamiento" in t for t in topics)


def test_sensitive_health_topic_excluded(tmp_path, monkeypatch):
    """Topic with health tag — never surfaces in nudge context."""
    import core.continuity_reentry as cr

    db = str(tmp_path / "gravity.db")
    monkeypatch.setenv("VECTRAX_GRAVITY_DB", db)

    _make_gravity_db(db, "tg:333", [
        {"id": "b1", "raw_text": "Diagnóstico medico de Mario",
         "summary": "Resultado consulta médica",
         "tags": ["salud"], "mass": 100.0},  # very high mass but sensitive
    ])

    topics = cr._load_gravity_topics("tg:333")
    assert topics == []  # excluded — never used in nudge


def test_sensitive_emotional_topic_excluded_by_keyword(tmp_path, monkeypatch):
    """Topic detected as sensitive via keyword scan even without tag."""
    import core.continuity_reentry as cr

    db = str(tmp_path / "gravity.db")
    monkeypatch.setenv("VECTRAX_GRAVITY_DB", db)

    _make_gravity_db(db, "tg:444", [
        {"id": "c1", "raw_text": "Me siento con mucha ansiedad últimamente",
         "tags": [],  # no tag, but keyword triggers filter
         "mass": 80.0},
    ])

    topics = cr._load_gravity_topics("tg:444")
    assert topics == []


def test_gravity_query_failure_falls_back_gracefully(monkeypatch):
    """If _load_gravity_topics raises internally, returns [] — never breaks nudge flow."""
    import core.continuity_reentry as cr

    # Point to a non-SQLite file to force a DB error
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        f.write(b"this is not sqlite")
        bad_db = f.name

    monkeypatch.setenv("VECTRAX_GRAVITY_DB", bad_db)
    topics = cr._load_gravity_topics("tg:555")
    assert topics == []
    os.unlink(bad_db)


def test_gravity_topics_injected_into_llm_prompt(tmp_path, monkeypatch):
    """When gravity topics exist, the LLM prompt contains them."""
    import core.continuity_reentry as cr

    db = str(tmp_path / "gravity.db")
    monkeypatch.setenv("VECTRAX_GRAVITY_DB", db)

    _make_gravity_db(db, "tg:666", [
        {"id": "d1", "raw_text": "Proyecto SaaS con Mario",
         "summary": "SaaS B2B expansion plan", "tags": ["negocio"], "mass": 60.0},
    ])

    captured: list[str] = []
    import vectrax.intelligence_bridge as ib
    monkeypatch.setattr(ib, "is_ready", lambda: True, raising=False)
    monkeypatch.setattr(
        ib, "route_single",
        lambda p: captured.append(p) or {"success": True, "content": "Qué tal va lo del SaaS."},
        raising=False,
    )

    cr._build_reentry_message("tg:666", nudge_number=2)
    assert len(captured) == 1
    assert "Temas de alta gravedad" in captured[0]
    assert "SaaS" in captured[0] or "expansion" in captured[0].lower()
