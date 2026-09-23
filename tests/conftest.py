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
    # Redirect the canonical conversation ledger (conversation_events table
    # in vault/user_memory.db). Root-cause fix (2026-09-20): tests exercising
    # ExternalGateway/pipeline_worker write REAL rows into the production
    # conversation_events table with synthetic test user_ids (u1, u2, ...) --
    # same isolation gap already fixed for op_cycles/observation_ledger/
    # user_memory above.
    try:
        import core.memory.conversation_ledger as _cl

        monkeypatch.setattr(
            _cl, "_DB_PATH", str(vault / "conversation_ledger.db"), raising=False
        )
        monkeypatch.setattr(_cl, "_repo", None, raising=False)
    except Exception:
        pass
    # Redirect the operational-cycle ledger (Pipeline Train's op_cycles.db).
    # Root-cause fix (2026-09-20): ANY test exercising `ExternalGateway
    # .receive_message()` (e.g. tests/test_external_gateway.py) writes real
    # rows into vault/operational_cycles.db with channel="telegram" and no
    # isolation -- indistinguishable in Pipeline Train from real production
    # traffic, and polluting the exact data this dashboard is meant to show
    # accurately. Same isolation pattern as user_memory/observation_ledger
    # above.
    try:
        import core.operational_cycle as _oc

        monkeypatch.setattr(
            _oc, "_DB_PATH", str(vault / "operational_cycles.db"), raising=False
        )
    except Exception:
        pass
    # Redirect the gravity index singleton (~/.vectrax/gravity_index.json)
    # and the Cognitive Consistency tracker (vault/cc_tracker.jsonl).
    # Root-cause fix (2026-09-20): NEITHER was covered by the isolation
    # above -- any test that runs a real convergence cycle (directly or via
    # NucleusAuthority) writes REAL rows into the production gravity index
    # and CC tracker with synthetic test fingerprints, and -- worse --
    # leaves that state behind for OTHER test files run later in the SAME
    # pytest process. That shared state changes what `total_convergence`
    # considers "evidencia suficiente" (is_novel/prior_patterns_found) for
    # an otherwise-identical prompt, causing order-dependent test flakiness
    # (e.g. test_nucleus_reunification.py diverging between channels only
    # when run after other test files touched the same fingerprint).
    # `GravityIndex`/`CCTracker` bind their default path at class-definition
    # time, so patching the module-level path constant alone is a no-op --
    # the singleton INSTANCE itself must be replaced.
    try:
        import core.learn.gravity_engine as _ge

        monkeypatch.setattr(
            _ge, "_index",
            _ge.GravityIndex(path=str(vault / "gravity_index.json")),
            raising=False,
        )
    except Exception:
        pass
    try:
        import core.learn.constitution as _const

        monkeypatch.setattr(
            _const, "_cc_tracker",
            _const.CCTracker(path=str(vault / "cc_tracker.jsonl")),
            raising=False,
        )
    except Exception:
        pass
    # Redirect the episodic ledger (append-only JSONL of convergence phase
    # events) -- same rationale as above, not decision-affecting but real
    # production data nonetheless.
    try:
        import core.learn.episodic as _epi

        monkeypatch.setattr(
            _epi, "_ledger",
            _epi.EpisodicLedger(path=str(vault / "episodic_ledger.jsonl")),
            raising=False,
        )
    except Exception:
        pass
    # Reset the TotalConvergenceEngine process-wide singleton. Root-cause fix
    # (PARTE 5, 2026-09-20): the singleton lazily caches ITS OWN sub-engines
    # (`_memory_engine`, `_hypothesis_engine`, `_rules_store`,
    # `_reasoning_engine`) on first use, and none of those are covered by the
    # gravity/CC/episodic isolation above -- they keep accumulating
    # cross-test state for the lifetime of the pytest process. `MemoryEngine
    # .query(intent=...)` matches by INTENT LABEL (not content fingerprint),
    # so an unrelated prompt classified with the same `intent_ssot` label in
    # an earlier test file can leak `related_patterns`/`connections` into a
    # later test's convergence cycle, flipping `is_novel`/`prior_patterns_
    # found` and, with it, whether `ANSWER_FROM_EVIDENCE` is proposed --
    # exactly the same class of order-dependent flakiness already fixed once
    # for gravity/CC above (test_nucleus_reunification.py diverging between
    # channels only when run after other test files). `reset_convergence_
    # engine()` drops the whole singleton so the next `get_convergence_
    # engine()` call starts with fresh sub-engines.
    try:
        import core.nucleus.total_convergence as _tce

        _tce.reset_convergence_engine()
    except Exception:
        pass

    # 2) Neutralize external credentials/config (deterministic, no real calls).
    for _key in _NEUTRALIZE_ENV:
        monkeypatch.delenv(_key, raising=False)

    # 3) Never auto-activate engines during tests (the API on_startup honours
    #    this flag). Keeps the suite hermetic — no operator/observer state
    #    written to the real ~/.vectrax when an app/TestClient is spun up.
    monkeypatch.setenv("VECTRAX_ACTIVATE_ENGINES", "off")

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


# ---------------------------------------------------------------------------
# Red de seguridad: la suite no crea el almacén de producción
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="session")
def _production_outcome_store_is_never_touched():
    """Falla la sesión si la suite CREA O MODIFICA
    `<vault de producción>/outcome_gravity.db`.

    `_hermetic_base` ya redirige `VECTRAX_VAULT_DIR` en cada prueba, y
    `core/learn/outcome_gravity.py` resuelve la ruta en cada llamada (hay
    pruebas que lo fijan). Aun así, durante el desarrollo de este almacén
    apareció una vez ese fichero en el vault de producción con filas de
    prueba, de forma INTERMITENTE: una traza sobre cada apertura de la base no
    llegó a dispararse en dos pasadas completas de la suite, así que no se
    identificó al responsable.

    Dar por resuelto lo que no se ha reproducido sería peor que dejarlo
    visible. Esta comprobación convierte esa fuga —venga de donde venga, de una
    prueba, de un hilo que sobrevive a su `monkeypatch` o de un subproceso con
    el entorno limpio— en un fallo ruidoso de la sesión.

    Compara el CONTENIDO, no solo la existencia. Una primera versión solo
    detectaba la creación del fichero: en una máquina donde el almacén ya
    existiera —que es justo el caso de producción— una prueba podía escribir
    dentro de él sin que nadie se enterara. Se comprueba el hash, así que
    detecta por igual la creación, la escritura y el borrado.

    Solo lee; no crea, no modifica y no borra nada.
    """
    import hashlib

    def _fingerprint(path):
        try:
            if not os.path.exists(path):
                return None
            with open(path, "rb") as fh:
                return hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            return "<ilegible>"

    try:
        from core.learn.outcome_gravity import (
            PRODUCTION_VAULT_DIR, STORE_FILENAME,
        )
    except Exception:
        yield
        return

    path = os.path.join(PRODUCTION_VAULT_DIR, STORE_FILENAME)
    before = _fingerprint(path)
    yield
    after = _fingerprint(path)
    if before == after:
        return
    if before is None:
        detail = "lo CREÓ"
    elif after is None:
        detail = "lo BORRÓ"
    else:
        detail = "ESCRIBIÓ en él"
    pytest.fail(
        f"La suite {detail}: almacén de PRODUCCIÓN {path}. Alguna prueba "
        "escapa a VECTRAX_VAULT_DIR: localizarla antes de fusionar."
    )
