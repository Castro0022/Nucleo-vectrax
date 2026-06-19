"""
Root pytest configuration for the Vectrax test suite.

Goal (architecture, not patches): make the suite HERMETIC and reproducible so
failures reflect real defects, not the developer machine's persistent state
(creator memory, vault DBs, ~/.vectrax config, ambient credentials).

Two layers are provided:

1. ``_hermetic_base`` (autouse): applied to every test. It redirects volatile
   writes (the observation ledger / vault) to a per-test temp directory and
   neutralizes external integration credentials so unit tests can never reach
   real services nor depend on ambient secrets. monkeypatch auto-reverts after
   each test, so nothing leaks between tests.

2. Opt-in fixtures (``stub_persistent_memory``, ``isolated_user_memory``,
   ``temp_vault``): test modules that touch the permanent creator memory or the
   per-user SQLite store request these explicitly. They isolate the persistent
   subsystems WITHOUT deleting or mutating the creator's real permanent memory.

IMPORTANT: nothing here wipes real permanent memory. Isolation is achieved by
redirecting paths / stubbing reads to temp scopes only.
"""
from __future__ import annotations

import os
import sys

import pytest

# Ensure the repo root is importable regardless of how pytest is invoked.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# External integration credentials/config that must never leak real values into
# unit tests. Tests that genuinely need one set it themselves (patch.dict /
# monkeypatch), which overrides this neutralization for that test's scope.
_NEUTRALIZE_ENV = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CREATOR_CHAT_ID",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_PLACES_API_KEY",
    "ANTHROPIC_API_KEY",
    "ETORO_API_KEY",
    "ETORO_USERNAME",
    "ETORO_ENVIRONMENT",
    "BROKER_PROVIDER",
)


@pytest.fixture(autouse=True)
def _hermetic_base(monkeypatch, tmp_path):
    """Per-test hermetic baseline: temp vault + neutral external credentials."""
    # 1) Redirect vault / observation ledger writes to a per-test temp dir.
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VECTRAX_VAULT_DIR", str(vault))
    # observation_ledger computes its DB path at import time; redirect the
    # module attribute too so already-imported code writes to the temp vault.
    try:
        import core.self_observation.observation_ledger as _ol

        monkeypatch.setattr(
            _ol, "_DB_PATH", str(vault / "observation_ledger.db"), raising=False
        )
    except Exception:
        pass
    # Redirect the per-user SQLite memory store to the temp vault and reset its
    # singleton, so tests that call store_memory()/clear_all_memory() can never
    # read OR WIPE the real per-user memory DB. (user_memory._MEMORY_DB_PATH is
    # computed from the module path and otherwise ignores VECTRAX_VAULT_DIR.)
    try:
        import vectrax.user_memory as _um

        monkeypatch.setattr(
            _um, "_MEMORY_DB_PATH", str(vault / "user_memory.db"), raising=False
        )
        monkeypatch.setattr(_um, "_store", None, raising=False)
    except Exception:
        pass

    # 2) Neutralize external credentials/config (deterministic, no real calls).
    for _key in _NEUTRALIZE_ENV:
        monkeypatch.delenv(_key, raising=False)

    yield


@pytest.fixture
def temp_vault(tmp_path, monkeypatch):
    """Opt-in: an isolated vault directory, returned as a Path."""
    vault = tmp_path / "vault_explicit"
    vault.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VECTRAX_VAULT_DIR", str(vault))
    return vault


@pytest.fixture
def stub_persistent_memory(monkeypatch):
    """Opt-in: isolate the permanent creator-memory + locked-language reads.

    Neutralizes (read-only) the persistent subsystems that ``resolve_with_memory``
    consults, so memory tests are deterministic on any machine WITHOUT touching
    the creator's real permanent memory:
      - core_memory.get_living_response  -> ""    (no persistent "soul")
      - identity_anchor.get_locked_language -> "" (no pre-locked language)
      - language_gate.get_user_language  -> language detected from the input
    """
    try:
        import vectrax.core_memory as _cm

        monkeypatch.setattr(_cm, "get_living_response", lambda *a, **k: "", raising=False)
    except Exception:
        pass
    try:
        import vectrax.identity_anchor as _ia

        monkeypatch.setattr(_ia, "get_locked_language", lambda *a, **k: "", raising=False)
    except Exception:
        pass
    try:
        import core.language_gate as _lg
        from vectrax.user_memory import _detect_lang

        monkeypatch.setattr(
            _lg,
            "get_user_language",
            lambda user_id, user_input="": _detect_lang(user_input),
            raising=False,
        )
    except Exception:
        pass
    yield


@pytest.fixture
def isolated_user_memory(tmp_path, monkeypatch):
    """Opt-in: point the per-user SQLite memory store at a fresh temp DB and
    reset its singleton so no real user data is read or written."""
    try:
        import vectrax.user_memory as _um

        db = tmp_path / "user_memory.db"
        monkeypatch.setattr(_um, "_MEMORY_DB_PATH", str(db), raising=False)
        monkeypatch.setattr(_um, "_store", None, raising=False)
        yield db
        monkeypatch.setattr(_um, "_store", None, raising=False)
    except Exception:
        yield None
