"""
Hermetic tests for the Pillar-C self-observation contract.

Covers:
  - quality_observer: domain contract, privacy sanitizer, event schemas, helpers
  - code_change_observer: commit / diff observation against a throwaway git repo

All writes land in the per-test temp vault provided by the autouse
``_hermetic_base`` fixture (see tests/conftest.py), so nothing touches the real
ledger. The git tests build a fresh temporary repository and never touch the
developer's real repos or global config.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

import core.self_observation.observation_ledger as ledger
import core.self_observation.quality_observer as qo
import core.self_observation.code_change_observer as cco


# ===========================================================================
# quality_observer — domain contract
# ===========================================================================

class TestDomainContract:
    @pytest.mark.parametrize("domain", ["test", "code", "quality", "runtime"])
    def test_known_domains_record(self, domain):
        row = qo.record_event(domain, "probe", "hola", evidence={"n": 1})
        assert row > 0
        got = ledger.get_by_domain(domain, limit=5)
        assert any(r["obs_type"] == "probe" for r in got)

    @pytest.mark.parametrize("domain", ["gravity", "market", "user", "nope"])
    def test_foreign_domains_rejected(self, domain):
        with pytest.raises(ValueError):
            qo.record_event(domain, "probe", "x")

    def test_unknown_severity_coerced_to_info(self):
        qo.record_event("quality", "sev_probe", "x", severity="bogus")
        row = ledger.get_by_domain("quality", limit=1)[0]
        assert row["severity"] == "info"


# ===========================================================================
# quality_observer — privacy sanitizer
# ===========================================================================

class TestSanitizer:
    def test_sensitive_keys_redacted(self):
        qo.record_event(
            "runtime", "leak_probe", "x",
            evidence={
                "api_key": "sk-secret-123",
                "password": "hunter2",
                "auth_token": "abc",
                "file": "core/x.py",  # benign, kept
            },
        )
        ev = ledger.get_by_domain("runtime", limit=1)[0]["evidence"]
        assert ev["api_key"] == "<redacted>"
        assert ev["password"] == "<redacted>"
        assert ev["auth_token"] == "<redacted>"
        assert ev["file"] == "core/x.py"

    def test_home_dir_stripped(self, monkeypatch):
        monkeypatch.setattr(qo, "_HOME", "/home/tester", raising=False)
        assert qo._abstract_str("/home/tester/proj/x.py") == "~/proj/x.py"

    def test_long_string_capped(self):
        assert len(qo._abstract_str("a" * 5000)) == qo._MAX_STR

    def test_unknown_type_becomes_placeholder(self):
        out = qo._sanitize({"obj": object(), "nums": {1, 2, 3}})
        assert out["obj"] == "<object>"
        assert sorted(out["nums"]) == [1, 2, 3]  # set coerced to list

    def test_nested_depth_guarded(self):
        deep = cur = {}
        for _ in range(20):
            cur["x"] = {}
            cur = cur["x"]
        # Should not raise / recurse forever.
        assert qo._sanitize(deep) is not None


# ===========================================================================
# quality_observer — test-domain event schemas
# ===========================================================================

class TestTestEvents:
    def test_failed_test_schema(self):
        qo.record_failed_test(
            "tests/test_mod.py::TestX::test_a", "run1",
            cause_class="ValueError", phase="call", duration_s=0.25,
        )
        row = ledger.get_by_domain("test", limit=1)[0]
        assert row["obs_type"] == "failed_test"
        assert row["severity"] == "warning"
        ev = row["evidence"]
        assert ev["file"] == "tests/test_mod.py"
        assert ev["scope"] == "tests"
        assert ev["cause_class"] == "ValueError"
        assert ev["run_id"] == "run1"
        assert ev["nodeid"].endswith("::test_a")

    def test_setup_failure_is_critical(self):
        qo.record_failed_test("tests/t.py::t", "r", phase="setup")
        row = ledger.get_by_domain("test", limit=1)[0]
        assert row["severity"] == "critical"

    def test_suite_end_is_info_summary_even_with_failures(self):
        qo.record_suite_end(
            "run9", total=10, passed=7, failed=3, skipped=0,
            errors=0, duration_s=1.2,
        )
        row = ledger.get_by_domain("test", limit=1)[0]
        assert row["obs_type"] == "suite_end"
        # Deliberate: summary stays neutral so it is not a failure star.
        assert row["severity"] == "info"
        assert row["evidence"]["success"] is False
        assert row["evidence"]["failed"] == 3

    def test_suite_end_success_flag(self):
        qo.record_suite_end("r", total=5, passed=5, failed=0, errors=0)
        ev = ledger.get_by_domain("test", limit=1)[0]["evidence"]
        assert ev["success"] is True

    def test_recovered_test_schema(self):
        qo.record_recovered_test("tests/test_a.py::test_z", "r2", prev_run_id="r1")
        row = ledger.get_by_domain("test", limit=1)[0]
        assert row["obs_type"] == "recovered_test"
        assert row["severity"] == "info"
        assert row["evidence"]["prev_run_id"] == "r1"
        assert row["evidence"]["file"] == "tests/test_a.py"


# ===========================================================================
# quality_observer — code-domain + helpers
# ===========================================================================

class TestCodeEventAndHelpers:
    def test_code_change_includes_modules(self):
        qo.record_code_change(
            files=["core/a.py", "core/b.py", "cli/c.py"],
            insertions=10, deletions=2, branch="feature/x", commit="abcdef123456",
        )
        row = ledger.get_by_domain("code", limit=1)[0]
        assert row["obs_type"] == "code_change"
        ev = row["evidence"]
        assert ev["file_count"] == 3
        assert ev["insertions"] == 10 and ev["deletions"] == 2
        assert ev["branch"] == "feature/x"
        assert ev["commit"] == "abcdef123456"
        # modules = distinct components; >1 ⇒ cross-module convergence.
        assert ev["modules"] == ["core", "cli"]

    def test_nodeid_to_file(self):
        assert qo.nodeid_to_file("a/b.py::Test::case") == "a/b.py"
        assert qo.nodeid_to_file("") == ""

    def test_path_scope(self):
        assert qo.path_scope("core/self_observation/x.py") == "core"
        assert qo.path_scope("top.py") == "top.py"

    def test_runtime_error_event(self):
        qo.record_runtime_error("TimeoutError", where="worker", module="pipeline")
        row = ledger.get_by_domain("runtime", limit=1)[0]
        assert row["obs_type"] == "runtime_error"
        assert row["severity"] == "warning"
        assert row["evidence"]["cause_class"] == "TimeoutError"
        assert row["evidence"]["module"] == "pipeline"


# ===========================================================================
# quality_observer — vault binding
# ===========================================================================

def test_bind_vault_dir_points_ledger(tmp_path, monkeypatch):
    target = tmp_path / "bound_vault"
    target.mkdir()
    bound = qo.bind_vault_dir(str(target))
    assert bound == str(target / "observation_ledger.db")
    assert ledger._DB_PATH == bound
    qo.record_event("quality", "bound_probe", "x")
    assert (target / "observation_ledger.db").exists()


# ===========================================================================
# code_change_observer — against a throwaway git repo
# ===========================================================================

_HAS_GIT = shutil.which("git") is not None
pytestmark_git = pytest.mark.skipif(not _HAS_GIT, reason="git not available")


def _init_repo(path):
    """Create an isolated git repo at ``path`` and return it (str)."""
    env = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(path / ".gitconfig_none"),
        "HOME": str(path),
        "PATH": __import__("os").environ.get("PATH", ""),
    }

    def run(*args):
        subprocess.run(
            ["git", *args], cwd=str(path), env=env,
            check=True, capture_output=True, text=True,
        )

    path.mkdir(parents=True, exist_ok=True)
    run("init", "-q")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Tester")
    run("config", "commit.gpgsign", "false")
    return str(path), run


@pytestmark_git
class TestCodeChangeObserver:
    def test_observe_commit_records_change(self, tmp_path):
        repo, run = _init_repo(tmp_path / "repo")
        pkg = tmp_path / "repo" / "core"
        pkg.mkdir()
        (pkg / "foo.py").write_text("x = 1\ny = 2\n")
        run("add", "core/foo.py")
        run("commit", "-q", "-m", "add foo")

        row = cco.observe_commit("HEAD", repo_dir=repo)
        assert row > 0
        ev = ledger.get_by_domain("code", limit=1)[0]["evidence"]
        assert ev["source"] == "commit"
        assert "core/foo.py" in ev["files"]
        assert ev["insertions"] == 2
        assert ev["commit"]  # short sha present
        assert ev["branch"]  # some branch name
        assert "core" in ev["modules"]

    def test_observe_diff_worktree(self, tmp_path):
        repo, run = _init_repo(tmp_path / "repo2")
        f = tmp_path / "repo2" / "a.py"
        f.write_text("a = 1\n")
        run("add", "a.py")
        run("commit", "-q", "-m", "init")
        # Uncommitted change in the working tree.
        f.write_text("a = 1\nb = 2\nc = 3\n")

        row = cco.observe_diff(repo_dir=repo)
        assert row > 0
        ev = ledger.get_by_domain("code", limit=1)[0]["evidence"]
        assert ev["source"] == "worktree"
        assert ev["insertions"] >= 2
        assert "a.py" in ev["files"]

    def test_observe_diff_clean_records_nothing(self, tmp_path):
        repo, run = _init_repo(tmp_path / "repo3")
        (tmp_path / "repo3" / "a.py").write_text("a = 1\n")
        run("add", "a.py")
        run("commit", "-q", "-m", "init")
        assert cco.observe_diff(repo_dir=repo) == 0
        assert ledger.get_by_domain("code", limit=5) == []

    def test_observe_commit_not_a_repo(self, tmp_path):
        not_repo = tmp_path / "plain"
        not_repo.mkdir()
        assert cco.observe_commit(repo_dir=str(not_repo)) == -1
