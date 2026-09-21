"""
tests/test_incident_20260920_identity_and_capacity_regression.py
==============================================================================
Regression suite for the real Telegram production incident of 2026-09-20,
19:31-19:32 (correlation_ids 25653679824e and 8c667c0b9f4a).

Exercises the FULL real pipeline exactly as production does it for Telegram:
`core.transport.pipeline_worker._process_one()` -- never an isolated internal
function in place of it -- so this suite also catches bugs that only manifest
from the INTERACTION between layers (anti_repetition, graceful degradation,
NucleusAuthority, personal memory retrieval), not just from any single layer
tested in isolation.

Incident 1 (correlation_id 25653679824e): "¿Quién soy yo?" from Mario's real
Telegram UID was answered "Eres Beltrán, y estás vinculado a Mario Bravo
Castro." Root cause: a question about the ASKER's own identity has no
deterministic override in `NucleusAuthority` (`_IDENTITY_SELF_RE` only covers
questions about VECTRAX's own identity). It falls through to
`is_personal_memory_query()` -> `Strategy.RESOLVE_PERSONAL_MEMORY` ->
`retrieve_personal_memory()`, a fuzzy fragment-retrieval+synthesis path.
`resolve_local()`'s profile-summary branch (`_select_profile_summary_stars()`)
does not filter fragments by relevance to "who am I" -- it aggregates ANY
recent fact about the owner via a generic "recent context" fallback,
including unrelated facts that happen to mention a different name. This is
reproduced deterministically (no live LLM needed): the real LLM call is
disabled in this suite so synthesis always falls back to the same
deterministic `_synthesize_local()` production code path, which simply
concatenates whatever fragments were retrieved.

Incident 2 (correlation_id 8c667c0b9f4a): "No, yo soy Mario, tú eres
Vectrax" (a plain MEMORY statement) was ingested correctly and produced the
internal confirmation "Registrado.", but `core.voice.anti_repetition
.filter_response()` flagged that short confirmation as too-similar to a
recently-sent response (ALL short MEMORY confirmations are near-identical to
each other for any active user) and returned `None`. `pipeline_worker
._process_one()` then fell to GRACEFUL_DEGRADATION and sent the misleading
"Capacidad de procesamiento limitada en este momento" message -- even though
the message was processed and stored successfully.

These tests reproduce BOTH incidents failing for their REAL causes, through
the combination of ALL real layers, with two distinct users to prove
isolation, and re-exercise the capability matrix (personal memory, Vectrax's
own identity, self-aware/gravity, market, domains, capabilities, online)
end-to-end so a future fix cannot silently regress any of them.

No mock stands in for NucleusAuthority, SmartRouter, resolve_local, the
convergence cycle, star_deriver, or anti_repetition -- only true external
boundaries are neutralized: the real Telegram HTTP call (never hit in
tests), the real LLM call (`_interpret_with_llm` -> "" so synthesis always
falls back to the deterministic `_synthesize_local`, avoiding
network/non-determinism), and real embeddings (deterministic word-overlap
fake, same technique as tests/test_e2e_conversational_memory.py) -- so tests
are hermetic and reproducible on any machine, while exercising the exact
same code paths as production.

DO NOT "fix" these tests by loosening assertions -- they are meant to FAIL
until the real causes are corrected (see module docstrings of
core/nucleus/nucleus_authority.py and core/transport/pipeline_worker.py).
"""
from __future__ import annotations

import importlib
import os
import re
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import vectrax.db as db  # noqa: E402
import vectrax.core_memory as core_memory  # noqa: E402
import vectrax.embeddings as embeddings  # noqa: E402
import core.voice.anti_repetition as anti_repetition  # noqa: E402
import core.transport.message_queue as message_queue  # noqa: E402
import core.transport.pipeline_worker as pipeline_worker  # noqa: E402
import core.nucleus.nucleus_authority as nucleus_authority_mod  # noqa: E402


CREATOR_UID = os.environ.get("VX_CREATOR_ID", "2030762343")

CAPACITY_MESSAGE_MARKERS = (
    "capacidad de procesamiento limitada",
    "processing capacity is limited",
)


def _contains_capacity_message(text: str) -> bool:
    t = (text or "").lower()
    return any(m in t for m in CAPACITY_MESSAGE_MARKERS)


def _fake_word_overlap_embed(text: str, dim: int = 64):
    """Deterministic fake embedding -- same technique as
    tests/test_e2e_conversational_memory.py. Never loads a real model;
    texts sharing significant words get high cosine similarity, unrelated
    texts get near-zero similarity -- same qualitative behavior as a real
    model, without its cost or non-determinism."""
    import hashlib

    import numpy as np

    vec = np.zeros(dim, dtype="float32")
    words = re.findall(r"[a-záéíóúñü]+", (text or "").lower())
    for w in words:
        if len(w) <= 2:
            continue
        h = int(hashlib.md5(w.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if (h // dim) % 2 == 0 else -1.0
        vec[idx] += sign
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    else:
        vec = np.ones(dim, dtype="float32") / np.sqrt(dim)
    return vec.astype("float32")


@pytest.fixture
def _isolated_full_stack(tmp_path, monkeypatch):
    """Isolates every persistent boundary `_process_one()` touches, and
    neutralizes the two true external systems (real Telegram HTTP, real
    LLM) WITHOUT mocking any internal decision-making code (NucleusAuthority,
    SmartRouter, resolve_local, convergence, star_deriver, anti_repetition
    all run for real, against isolated storage)."""
    # -- vectrax.db (stars + star_provenance + identity_aliases) ----------
    tmp_db = tmp_path / "vectrax.db"
    monkeypatch.setattr(db, "DB_DIR", tmp_path, raising=True)
    monkeypatch.setattr(db, "DB_PATH", tmp_db, raising=True)
    db.init_db()

    monkeypatch.setattr(
        core_memory, "_DB_PATH", str(tmp_path / "user_memory_core.db"), raising=True,
    )
    import vectrax.identity_aliases as identity_aliases
    monkeypatch.setattr(identity_aliases, "DB_PATH", tmp_db, raising=True)

    # -- anti-repetition ring buffer -- NEVER touch the real one, least of
    #    all when a test uses the real creator UID.
    monkeypatch.setattr(
        anti_repetition, "_DB_PATH", str(tmp_path / "anti_repetition.db"), raising=True,
    )

    # -- message queue: `_process_one()` calls mark_done/mark_error via a
    #    LOCAL import each time, so patching the module attributes here is
    #    picked up without touching vault/message_queue.db.
    done_calls = []
    error_calls = []
    monkeypatch.setattr(
        message_queue, "mark_done",
        lambda mid, resp: done_calls.append((mid, resp)), raising=True,
    )
    monkeypatch.setattr(
        message_queue, "mark_error",
        lambda mid, err: error_calls.append((mid, err)), raising=True,
    )

    # -- Telegram: capture what WOULD be sent, never hit the real API
    #    (regardless of whether a real TELEGRAM_BOT_TOKEN happens to be
    #    loaded in this process's environment).
    sent = []

    def _fake_tg_send(chat_id, text, **kwargs):
        sent.append((chat_id, text))
        return True

    monkeypatch.setattr(pipeline_worker, "_tg_send", _fake_tg_send, raising=True)
    monkeypatch.setattr(pipeline_worker, "_tg_venue", lambda *a, **k: False, raising=True)
    monkeypatch.setattr(pipeline_worker, "_tg_chat_action", lambda *a, **k: None, raising=True)

    # -- Real LLM: never called in this suite -- keeps assertions about
    #    STRUCTURE/ROUTING deterministic instead of depending on a live
    #    model's exact wording or availability. The deterministic
    #    `_synthesize_local()` fallback (the SAME code path production uses
    #    whenever the LLM/constitutional gate is unavailable) is what
    #    actually gets exercised -- and it is enough to reproduce the real
    #    structural bug (irrelevant memory leaking into an identity answer).
    import vectrax.resolver as vx_resolver
    monkeypatch.setattr(vx_resolver, "_interpret_with_llm", lambda *a, **k: "", raising=True)

    # -- Fake deterministic embeddings. `vectrax/engine.py` does a
    #    MODULE-LEVEL `from vectrax.embeddings import embed` (frozen at
    #    first import), so the patch must be applied AND the module
    #    reloaded for `ingest()`/star creation to pick it up.
    import vectrax.engine as vx_engine
    embed_patch = patch.object(embeddings, "embed", side_effect=_fake_word_overlap_embed)
    embed_patch.start()
    importlib.reload(vx_engine)

    # -- NucleusResponse capture: lets tests assert on tool_executed/
    #    final_action IN ADDITION to the actual Telegram-sent text, without
    #    mocking the decision itself (wraps the REAL method, records its
    #    real return value).
    captured_responses = []
    original_resolve_from_record = nucleus_authority_mod.NucleusAuthority.resolve_from_record

    def _capturing_resolve_from_record(self, *a, **kw):
        resp = original_resolve_from_record(self, *a, **kw)
        captured_responses.append(resp)
        return resp

    monkeypatch.setattr(
        nucleus_authority_mod.NucleusAuthority, "resolve_from_record",
        _capturing_resolve_from_record, raising=True,
    )

    try:
        yield {
            "tmp_path": tmp_path,
            "sent": sent,
            "done_calls": done_calls,
            "error_calls": error_calls,
            "nucleus_responses": captured_responses,
        }
    finally:
        embed_patch.stop()
        importlib.reload(vx_engine)


def send_telegram(ctx: dict, user_id: str, chat_id: int, content: str) -> str:
    """Simulates a REAL Telegram message through the exact production
    entrypoint (`pipeline_worker._process_one()`), and returns the text
    that was actually sent back to the user (captured via the mocked
    `_tg_send` -- never a mocked internal decision)."""
    from core.transport.message_queue import QueueMessage

    before = len(ctx["sent"])
    msg = QueueMessage(
        id=uuid.uuid4().hex[:12], user_id=user_id, chat_id=chat_id,
        content=content, channel="telegram",
    )
    ok = pipeline_worker._process_one(msg)
    assert ok is True, f"_process_one returned False for {content!r}"
    assert len(ctx["sent"]) > before, f"no message was sent for {content!r}"
    return ctx["sent"][-1][1]


def last_nucleus_response(ctx: dict):
    assert ctx["nucleus_responses"], "no NucleusResponse captured yet"
    return ctx["nucleus_responses"][-1]


# ---------------------------------------------------------------------------
# Incident 1 (correlation_id 25653679824e): "¿Quién soy yo?" -> "Eres Beltrán"
# ---------------------------------------------------------------------------

class TestIdentityIncidentRegression:
    def test_creator_identity_question_never_confuses_with_unrelated_name(
        self, _isolated_full_stack,
    ):
        ctx = _isolated_full_stack
        owner = f"tg:{CREATOR_UID}"
        chat_id = 100001

        # Unrelated fact mentioning a DIFFERENT name -- structurally
        # identical to the real incident: a piece of REAL memory (a
        # business contact's name) must never bleed into the answer to
        # "who am I".
        send_telegram(ctx, owner, chat_id, "Mi socio se llama Beltrán.")

        # NOTE: deliberately without a leading inverted question mark --
        # matches the real incident's reconstructed Telegram text
        # ("Quién soy yo ?", see worker.log) and the exact anchored pattern
        # `vectrax.resolver._PROFILE_SUMMARY_RE` requires (`^\s*qui[eé]n\s+
        # soy...`) to classify this as a profile-summary/personal-memory
        # query in the first place.
        answer = send_telegram(ctx, owner, chat_id, "Quién soy yo?")

        assert "beltrán" not in answer.lower() and "beltran" not in answer.lower(), (
            f"identity answer leaked an unrelated stored name: {answer!r}"
        )
        assert "mario bravo castro" in answer.lower(), (
            f"creator's own identity question must resolve deterministically "
            f"to the full canonical name 'Mario Bravo Castro': {answer!r}"
        )
        assert not _contains_capacity_message(answer)

        resp = last_nucleus_response(ctx)
        assert resp.final_action == "IDENTITY"
        assert resp.tool_executed != "retrieve_personal_memory", (
            "a question about the ASKER's own identity must resolve "
            "deterministically (channel -> identity_aliases -> canonical "
            "owner), never via the fuzzy personal-memory retrieval path "
            f"(tool_executed={resp.tool_executed!r})"
        )
        assert "identity_anchor" in resp.memory_source.lower() or "identity_aliases" in resp.memory_source.lower(), (
            f"identity resolution must be sourced from identity_anchor/"
            f"identity_aliases, not from fuzzy memory: {resp.memory_source!r}"
        )

    def test_second_user_identity_question_returns_their_own_name_never_creators(
        self, _isolated_full_stack,
    ):
        ctx = _isolated_full_stack
        user_b = f"tg:{999000111}"
        chat_id = 100002

        send_telegram(ctx, user_b, chat_id, "Me llamo Laura.")
        answer = send_telegram(ctx, user_b, chat_id, "Quién soy yo?")

        assert "laura" in answer.lower(), (
            f"a non-creator user's identity question must resolve to their "
            f"own registered name: {answer!r}"
        )
        assert "mario" not in answer.lower(), (
            f"non-creator identity answer leaked the creator's identity: {answer!r}"
        )
        assert "beltrán" not in answer.lower() and "beltran" not in answer.lower()
        assert not _contains_capacity_message(answer)

        resp = last_nucleus_response(ctx)
        assert resp.tool_executed != "retrieve_personal_memory"

    def test_two_users_identity_never_cross_contaminates(self, _isolated_full_stack):
        """Isolation, demonstrated with BOTH incident-style users in the
        same run: neither identity nor unrelated stored names leak across
        users."""
        ctx = _isolated_full_stack
        owner_mario = f"tg:{CREATOR_UID}"
        user_b = f"tg:{999000222}"

        send_telegram(ctx, owner_mario, 100003, "Mi socio se llama Beltrán.")
        send_telegram(ctx, user_b, 100004, "Me llamo Laura.")

        answer_mario = send_telegram(ctx, owner_mario, 100003, "Quién soy yo?")
        answer_b = send_telegram(ctx, user_b, 100004, "Quién soy yo?")

        assert "mario" in answer_mario.lower()
        assert "laura" not in answer_mario.lower()
        assert "beltrán" not in answer_mario.lower() and "beltran" not in answer_mario.lower()

        assert "laura" in answer_b.lower()
        assert "mario" not in answer_b.lower()

    def test_vectrax_own_identity_question_still_resolves_correctly(
        self, _isolated_full_stack,
    ):
        """Non-regression: the EXISTING deterministic override for questions
        about Vectrax's OWN identity (`_IDENTITY_SELF_RE`) must keep working
        once a NEW override is added for the USER's own identity -- the two
        must never be conflated into the same code path."""
        ctx = _isolated_full_stack
        owner = f"tg:{CREATOR_UID}"

        answer = send_telegram(ctx, owner, 100005, "¿Quién eres?")

        assert answer.strip() != ""
        assert not _contains_capacity_message(answer)
        resp = last_nucleus_response(ctx)
        assert resp.final_action in ("IDENTITY", "LOCAL"), (
            f"'¿Quién eres?' must keep resolving as Vectrax's own identity, "
            f"got final_action={resp.final_action!r}"
        )
        assert resp.tool_executed != "resolve_online"


# ---------------------------------------------------------------------------
# Incident 2 (correlation_id 8c667c0b9f4a): fake "capacidad limitada"
# ---------------------------------------------------------------------------

class TestGracefulDegradationIncidentRegression:
    def test_memory_confirmation_never_shows_fake_capacity_message_when_deduplicated(
        self, _isolated_full_stack,
    ):
        """Reproduces the REAL trigger: any user who recently received a
        short MEMORY confirmation ("Registrado.") gets a near-identical one
        for their NEXT unrelated note -- exactly what happened to Mario
        seconds before the real incident's second message."""
        ctx = _isolated_full_stack
        user_id = f"tg:{CREATOR_UID}"
        chat_id = 200001

        send_telegram(ctx, user_id, chat_id, "Mi color favorito es el verde.")
        second_answer = send_telegram(ctx, user_id, chat_id, "Mi ciudad favorita es Lisboa.")

        assert not _contains_capacity_message(second_answer), (
            f"a normally-processed MEMORY statement must never surface the "
            f"misleading capacity-limited fallback: {second_answer!r}"
        )
        assert second_answer.strip() != "", "user must never be left with silence"

    def test_exact_incident_message_never_shows_fake_capacity_message(
        self, _isolated_full_stack,
    ):
        """Direct reproduction using the literal incident text, with the
        anti-repetition ring buffer pre-seeded exactly as it was for Mario
        moments before the real incident (a recent 'Registrado.')."""
        ctx = _isolated_full_stack
        user_id = f"tg:{CREATOR_UID}"
        chat_id = 200002

        anti_repetition.record_response(user_id, "Registrado.")

        answer = send_telegram(
            ctx, user_id, chat_id, "No, yo soy Mario, tú eres Vectrax.",
        )

        assert not _contains_capacity_message(answer), (
            f"exact incident message must never surface the misleading "
            f"capacity-limited fallback: {answer!r}"
        )
        assert answer.strip() != ""

        # Corrección 2026-09-20 (identidad): esta frase es una CORRECCIÓN de
        # identidad, no una nota para guardar -- nunca debe clasificarse
        # como STORE ("Registrado."/"Actualizado."), y la respuesta debe
        # reconocer ambas identidades explícitamente.
        assert "registrado" not in answer.lower() and "actualizado" not in answer.lower(), (
            f"identity correction must never be treated as a memory-store "
            f"acknowledgment: {answer!r}"
        )
        assert "mario bravo castro" in answer.lower(), (
            f"identity correction answer must name the creator's full "
            f"canonical name: {answer!r}"
        )
        assert "vectrax core" in answer.lower(), (
            f"identity correction answer must name Vectrax's own identity: {answer!r}"
        )
        resp = last_nucleus_response(ctx)
        assert resp.final_action == "IDENTITY", (
            f"'No, yo soy Mario, tú eres Vectrax' must resolve as IDENTITY, "
            f"never MEMORY/STORE, got final_action={resp.final_action!r}"
        )

    def test_first_time_memory_confirmation_is_unaffected_baseline(
        self, _isolated_full_stack,
    ):
        """Sanity baseline: a user's FIRST note (nothing yet in the
        anti-repetition ring buffer) must confirm normally -- this already
        passes today and must keep passing after the fix."""
        ctx = _isolated_full_stack
        user_id = f"tg:{888000111}"
        chat_id = 200003

        answer = send_telegram(ctx, user_id, chat_id, "Mi ciudad favorita es Lisboa.")

        assert not _contains_capacity_message(answer)
        assert "registrado" in answer.lower() or "actualizado" in answer.lower()


# ---------------------------------------------------------------------------
# Capability matrix -- must keep working end-to-end through the FULL
# pipeline (not just NucleusAuthority in isolation) after the fix.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Contaminated identity cache must always re-anchor to the creator's
# canonical name (requirement from the 2026-09-20 incident follow-up).
# ---------------------------------------------------------------------------

class TestContaminatedIdentityCacheReanchors:
    def test_identity_anchor_ignores_contaminated_profile_name_for_creator(
        self, monkeypatch,
    ):
        """Unit-level: even if `user_memory.get_user_profile()` returns a
        WRONG name for the creator's own UID (e.g. 'Beltrán', contaminated
        by a bad extraction elsewhere), `get_anchored_identity()` must
        always resolve the creator's name to the canonical
        'Mario Bravo Castro' -- never to whatever is cached/stored."""
        import vectrax.identity_anchor as identity_anchor

        # The session cache is a process-wide singleton shared across tests
        # -- invalidate this UID first so a previous test's cached anchor
        # can't hide the bug this test exists to catch.
        identity_anchor._session.invalidate(f"tg:{CREATOR_UID}")

        monkeypatch.setattr(
            "vectrax.user_memory.get_user_profile",
            lambda user_id: {"name": "Beltrán", "language": "es"},
            raising=False,
        )
        monkeypatch.setattr(
            identity_anchor, "_load_creator_seed",
            lambda: {"creator_name": "Mario Bravo Castro"}, raising=True,
        )

        anchor = identity_anchor.get_anchored_identity(f"tg:{CREATOR_UID}")

        assert anchor.is_creator is True
        assert anchor.name == "Mario Bravo Castro", (
            f"creator identity must never be overridden by a cached/"
            f"contaminated profile name, got name={anchor.name!r}"
        )

    def test_creator_identity_question_reanchors_despite_contaminated_profile(
        self, _isolated_full_stack, monkeypatch,
    ):
        """End-to-end: the SAME contamination, exercised through the real
        Telegram pipeline -- 'Quién soy yo?' must still answer
        'Mario Bravo Castro', never 'Beltrán', even when the underlying
        profile store is already contaminated before the question arrives."""
        ctx = _isolated_full_stack
        owner = f"tg:{CREATOR_UID}"

        import vectrax.identity_anchor as identity_anchor
        identity_anchor._session.invalidate(owner)

        monkeypatch.setattr(
            "vectrax.user_memory.get_user_profile",
            lambda user_id: {"name": "Beltrán", "language": "es"},
            raising=False,
        )

        answer = send_telegram(ctx, owner, 400001, "Quién soy yo?")

        assert "beltrán" not in answer.lower() and "beltran" not in answer.lower(), (
            f"contaminated profile name leaked into the creator's identity "
            f"answer: {answer!r}"
        )
        assert "mario" in answer.lower()
        assert not _contains_capacity_message(answer)


class TestCapabilityMatrixNoRegressionFullStack:
    def test_personal_memory_recall_through_full_pipeline(self, _isolated_full_stack):
        ctx = _isolated_full_stack
        user_id = f"tg:{777000111}"
        chat_id = 300001

        send_telegram(ctx, user_id, chat_id, "Mi comida favorita es el ceviche.")
        answer = send_telegram(ctx, user_id, chat_id, "¿Qué sabes de mí?")

        assert "ceviche" in answer.lower()
        assert not _contains_capacity_message(answer)
        resp = last_nucleus_response(ctx)
        assert resp.final_action == "PERSONAL_MEMORY", (
            f"'¿Qué sabes de mí?' must keep resolving as PERSONAL_MEMORY "
            f"after the identity-precedence fix, got "
            f"final_action={resp.final_action!r}"
        )

    def test_vectrax_own_identity_question(self, _isolated_full_stack):
        ctx = _isolated_full_stack
        answer = send_telegram(ctx, f"tg:{CREATOR_UID}", 300002, "¿Quién eres?")

        assert answer.strip() != ""
        assert not _contains_capacity_message(answer)
        resp = last_nucleus_response(ctx)
        assert resp.final_action in ("IDENTITY", "LOCAL")
        assert resp.tool_executed != "resolve_online"

    def test_self_aware_gravity_question(self, _isolated_full_stack):
        ctx = _isolated_full_stack
        answer = send_telegram(
            ctx, f"tg:{CREATOR_UID}", 300003, "¿Qué significa la gravedad para ti?",
        )
        assert "gravedad" in answer.lower() or "gravity" in answer.lower()
        assert not _contains_capacity_message(answer)
        resp = last_nucleus_response(ctx)
        assert resp.tool_executed in ("self_knowledge", "")

    def test_capabilities_question(self, _isolated_full_stack):
        ctx = _isolated_full_stack
        answer = send_telegram(ctx, f"tg:{CREATOR_UID}", 300004, "¿Qué capacidades tienes?")
        assert answer.strip() != ""
        assert not _contains_capacity_message(answer)

    def test_domains_question(self, _isolated_full_stack):
        ctx = _isolated_full_stack
        answer = send_telegram(ctx, f"tg:{CREATOR_UID}", 300005, "¿Cuántos dominios tienes?")
        assert answer.strip() != ""
        assert not _contains_capacity_message(answer)

    def test_market_question_tolerant_of_data_availability(self, _isolated_full_stack):
        """Same honest tolerance as tests/test_nucleus_reunification.py: does
        not assert specific market content (may be unavailable in a test
        environment), only that the real pipeline never substitutes the
        fake capacity-limited message for a legitimate answer/abstention."""
        ctx = _isolated_full_stack
        answer = send_telegram(ctx, f"tg:{CREATOR_UID}", 300006, "Háblame de mercado")
        assert answer.strip() != ""
        assert not _contains_capacity_message(answer)
        resp = last_nucleus_response(ctx)
        assert resp.final_action != "IDENTITY" or resp.tool_executed not in (
            "user_identity_resolver",
        ), (
            "the new identity overrides must never hijack a market question "
            f"(final_action={resp.final_action!r}, tool_executed={resp.tool_executed!r})"
        )

    def test_domains_and_capabilities_keep_self_knowledge_routes(self, _isolated_full_stack):
        """Explicit route-preservation check: the new identity overrides
        (_USER_IDENTITY_SELF_RE / _IDENTITY_CORRECTION_RE) must never shadow
        the existing domains/capabilities self-knowledge routes."""
        ctx = _isolated_full_stack

        answer_caps = send_telegram(ctx, f"tg:{CREATOR_UID}", 300008, "¿Qué capacidades tienes?")
        resp_caps = last_nucleus_response(ctx)
        assert answer_caps.strip() != "" and not _contains_capacity_message(answer_caps)
        assert resp_caps.tool_executed == "capability_narrator"

        answer_doms = send_telegram(ctx, f"tg:{CREATOR_UID}", 300009, "¿Cuántos dominios tienes?")
        resp_doms = last_nucleus_response(ctx)
        assert answer_doms.strip() != "" and not _contains_capacity_message(answer_doms)
        assert resp_doms.tool_executed == "domain_census"

    def test_online_question_tolerant_of_network_availability(self, _isolated_full_stack):
        """Same honest tolerance as tests/test_nucleus_reunification.py:
        ONLINE/LOCAL/CLARIFICATION are all legitimate outcomes depending on
        network availability -- the point is that a genuine external-fact
        question is never mishandled by the fixes for the two incidents
        above."""
        ctx = _isolated_full_stack
        answer = send_telegram(
            ctx, f"tg:{999000333}", 300007,
            "¿Quién es actualmente el presidente de Francia?",
        )
        assert answer.strip() != ""
        resp = last_nucleus_response(ctx)
        assert resp.final_action in ("ONLINE", "LOCAL", "CLARIFICATION")


# ---------------------------------------------------------------------------
# Explicit, verifiable PASS/FAIL criterion (2026-09-21 follow-up): the most
# severe failure observed tonight was NOT the same-user "Beltrán" fragment
# leak above -- it was "¿Qué sabes de mí?" returning a COMPLETELY DIFFERENT
# user's identity (a real case: "Brenda Romanelo"). "Identidad y memoria
# producen respuestas coherentes" is too vague to catch this -- the
# verifiable criterion is: ninguna respuesta sobre identidad propia puede
# contener datos de un perfil de usuario distinto al que hace la pregunta.
# ---------------------------------------------------------------------------

class TestNoCrossUserIdentityLeak:
    def test_own_identity_question_never_contains_a_different_users_name(
        self, _isolated_full_stack,
    ):
        ctx = _isolated_full_stack
        other_user = f"tg:{555000111}"
        asker = f"tg:{CREATOR_UID}"

        # A COMPLETELY different user states their own identity first.
        send_telegram(ctx, other_user, 500001, "Me llamo Brenda Romanelo.")

        # The asker (creator) has no relation to that user whatsoever.
        answer_who = send_telegram(ctx, asker, 500002, "Quién soy yo?")
        answer_what = send_telegram(ctx, asker, 500003, "¿Qué sabes de mí?")

        for label, answer in (("Quién soy yo?", answer_who), ("¿Qué sabes de mí?", answer_what)):
            assert "brenda" not in answer.lower() and "romanelo" not in answer.lower(), (
                f"PASS/FAIL criterion violated: a response about the asker's "
                f"own identity contained a DIFFERENT user's profile data "
                f"({label!r} -> {answer!r})"
            )
            assert not _contains_capacity_message(answer)

    def test_own_identity_question_never_contains_a_different_users_name_reverse(
        self, _isolated_full_stack,
    ):
        """Same criterion, roles reversed: the user who owns the 'Brenda
        Romanelo' identity must see only their OWN identity, never the
        creator's or any third party's."""
        ctx = _isolated_full_stack
        brenda = f"tg:{555000222}"

        send_telegram(ctx, f"tg:{CREATOR_UID}", 500004, "Mi socio se llama Beltrán.")
        send_telegram(ctx, brenda, 500005, "Me llamo Brenda Romanelo.")

        answer_who = send_telegram(ctx, brenda, 500006, "Quién soy yo?")
        answer_what = send_telegram(ctx, brenda, 500007, "¿Qué sabes de mí?")

        for label, answer in (("Quién soy yo?", answer_who), ("¿Qué sabes de mí?", answer_what)):
            assert "brenda" in answer.lower() or "romanelo" in answer.lower(), (
                f"{label!r} must reflect the asker's OWN identity: {answer!r}"
            )
            assert "mario" not in answer.lower(), (
                f"PASS/FAIL criterion violated: {label!r} leaked the creator's "
                f"identity into another user's own-identity answer: {answer!r}"
            )
            assert "beltrán" not in answer.lower() and "beltran" not in answer.lower()
            assert not _contains_capacity_message(answer)
