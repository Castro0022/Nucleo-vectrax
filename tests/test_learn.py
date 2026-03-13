"""
Tests for Vectrax Learn Subsystem
===================================
Covers: scrubber, intent_engine, episodic, ascension_gate,
        ingestion (structure extraction), semantic (mocked).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import shutil
import unittest

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ===================================================================
# Scrubber
# ===================================================================

class TestScrubber(unittest.TestCase):

    def test_clean_text(self):
        from core.learn.scrubber import scrub, Sensitivity
        result = scrub("def hello(): pass")
        self.assertEqual(result.sensitivity, Sensitivity.CLEAN)
        self.assertEqual(result.findings, [])
        self.assertIn("hello", result.text)

    def test_detects_api_key(self):
        from core.learn.scrubber import scrub, Sensitivity
        text = 'api_key = "sk-abcdefgh12345678"'
        result = scrub(text)
        self.assertEqual(result.sensitivity, Sensitivity.SECRETS)
        self.assertIn("generic_api_key", result.findings)
        self.assertNotIn("sk-abcdefgh12345678", result.text)
        self.assertIn("[REDACTED_SECRET]", result.text)

    def test_detects_aws_key(self):
        from core.learn.scrubber import scrub, Sensitivity
        text = "AKIAIOSFODNN7EXAMPLE"
        result = scrub(text)
        self.assertEqual(result.sensitivity, Sensitivity.SECRETS)
        self.assertIn("aws_key", result.findings)

    def test_detects_email(self):
        from core.learn.scrubber import scrub, Sensitivity
        text = "Contact admin@example.com for support"
        result = scrub(text)
        self.assertIn("email", result.findings)
        self.assertNotIn("admin@example.com", result.text)
        self.assertIn("[REDACTED_PII]", result.text)

    def test_detects_password(self):
        from core.learn.scrubber import scrub, Sensitivity
        text = 'password = "supersecret123"'
        result = scrub(text)
        self.assertEqual(result.sensitivity, Sensitivity.SECRETS)
        self.assertIn("password_assign", result.findings)

    def test_detects_jwt(self):
        from core.learn.scrubber import scrub, Sensitivity
        text = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        result = scrub(text)
        self.assertEqual(result.sensitivity, Sensitivity.SECRETS)
        self.assertIn("jwt", result.findings)

    def test_is_sensitive_helper(self):
        from core.learn.scrubber import is_sensitive
        self.assertFalse(is_sensitive("clean code"))
        self.assertTrue(is_sensitive("api_key = sk12345678abcdef"))


# ===================================================================
# Intent Engine
# ===================================================================

class TestIntentEngine(unittest.TestCase):

    def test_unknown_intent(self):
        from core.learn.intent_engine import infer_intent
        result = infer_intent("x = 42")
        self.assertEqual(result.intent_primary, "UNKNOWN")
        self.assertGreater(result.confidence, 0.0)
        self.assertLessEqual(result.confidence, 1.0)

    def test_test_intent_from_content(self):
        from core.learn.intent_engine import infer_intent
        text = "def test_foo():\n    assert bar() == 42\n    mock.patch()"
        result = infer_intent(text)
        self.assertEqual(result.intent_primary, "TEST")

    def test_test_intent_from_path(self):
        from core.learn.intent_engine import infer_intent
        result = infer_intent("def hello(): pass", file_path="tests/test_foo.py")
        self.assertEqual(result.intent_primary, "TEST")

    def test_security_intent(self):
        from core.learn.intent_engine import infer_intent
        text = "encrypt credentials with auth token"
        result = infer_intent(text)
        self.assertEqual(result.intent_primary, "SECURITY")
        self.assertEqual(result.impact, "high")
        self.assertEqual(result.mode, "OBSERVE")  # high impact → OBSERVE

    def test_bug_fix_intent(self):
        from core.learn.intent_engine import infer_intent
        text = "fix bug in error handling, resolve crash issue"
        result = infer_intent(text)
        self.assertEqual(result.intent_primary, "BUG_FIX")

    def test_confidence_range(self):
        from core.learn.intent_engine import infer_intent
        result = infer_intent("add new feature to create module implement")
        self.assertGreater(result.confidence, 0.0)
        self.assertLessEqual(result.confidence, 1.0)

    def test_fingerprint_deterministic(self):
        from core.learn.intent_engine import infer_intent
        r1 = infer_intent("fix bug")
        r2 = infer_intent("fix bug")
        self.assertEqual(r1.fingerprint, r2.fingerprint)

    def test_sensitive_text_mode_observe(self):
        from core.learn.intent_engine import infer_intent
        text = "api_key = sk-abcdefgh12345678 fix bug"
        result = infer_intent(text)
        self.assertEqual(result.mode, "OBSERVE")
        self.assertEqual(result.sensitivity, "SECRETS")


# ===================================================================
# Episodic Memory
# ===================================================================

class TestEpisodicLedger(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ledger_path = os.path.join(self.tmpdir, "test_ledger.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_append_and_read(self):
        from core.learn.episodic import EpisodicLedger, EpisodicEvent
        ledger = EpisodicLedger(path=self.ledger_path)

        ev = EpisodicEvent(
            event_type="FILE_OBSERVED",
            summary="Observed test.py",
            content_hash="abc123",
        )
        eid = ledger.append(ev)
        self.assertEqual(len(eid), 12)

        events = ledger.read_all()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "FILE_OBSERVED")
        self.assertEqual(events[0]["summary"], "Observed test.py")

    def test_count(self):
        from core.learn.episodic import EpisodicLedger, EpisodicEvent
        ledger = EpisodicLedger(path=self.ledger_path)
        self.assertEqual(ledger.count(), 0)
        ledger.append(EpisodicEvent(event_type="FILE_OBSERVED", summary="a"))
        ledger.append(EpisodicEvent(event_type="INTENT_INFERRED", summary="b"))
        self.assertEqual(ledger.count(), 2)

    def test_invalid_event_type(self):
        from core.learn.episodic import EpisodicLedger, EpisodicEvent
        ledger = EpisodicLedger(path=self.ledger_path)
        ev = EpisodicEvent(event_type="INVALID_TYPE", summary="x")
        with self.assertRaises(ValueError):
            ledger.append(ev)

    def test_count_by_type(self):
        from core.learn.episodic import EpisodicLedger, EpisodicEvent
        ledger = EpisodicLedger(path=self.ledger_path)
        ledger.append(EpisodicEvent(event_type="FILE_OBSERVED", summary="a"))
        ledger.append(EpisodicEvent(event_type="FILE_OBSERVED", summary="b"))
        ledger.append(EpisodicEvent(event_type="INTENT_INFERRED", summary="c"))
        counts = ledger.count_by_type()
        self.assertEqual(counts["FILE_OBSERVED"], 2)
        self.assertEqual(counts["INTENT_INFERRED"], 1)

    def test_content_hash(self):
        from core.learn.episodic import content_hash
        h = content_hash("hello world")
        self.assertEqual(len(h), 16)
        self.assertEqual(h, content_hash("hello world"))  # deterministic
        self.assertNotEqual(h, content_hash("different"))


# ===================================================================
# Ascension Gate
# ===================================================================

class TestAscensionGate(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.candidates_path = os.path.join(self.tmpdir, "test_candidates.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_add_quarantined(self):
        from core.learn.ascension_gate import AscensionGate, PatternCandidate
        gate = AscensionGate(path=self.candidates_path)
        c = PatternCandidate(summary="test pattern", intent="TEST")
        cid = gate.add(c)
        self.assertEqual(len(cid), 8)

        cand = gate.get(cid)
        self.assertIsNotNone(cand)
        self.assertEqual(cand["state"], "QUARANTINED")

    def test_approve(self):
        from core.learn.ascension_gate import AscensionGate, PatternCandidate
        gate = AscensionGate(path=self.candidates_path)
        c = PatternCandidate(summary="approve me", intent="REFACTOR")
        cid = gate.add(c)

        self.assertTrue(gate.approve(cid))
        cand = gate.get(cid)
        self.assertEqual(cand["state"], "APPROVED")
        self.assertIsNotNone(cand["decided_at"])

    def test_ban(self):
        from core.learn.ascension_gate import AscensionGate, PatternCandidate
        gate = AscensionGate(path=self.candidates_path)
        c = PatternCandidate(summary="ban me", intent="UNKNOWN")
        cid = gate.add(c)

        self.assertTrue(gate.ban(cid))
        cand = gate.get(cid)
        self.assertEqual(cand["state"], "BANNED")

    def test_nonexistent_id(self):
        from core.learn.ascension_gate import AscensionGate
        gate = AscensionGate(path=self.candidates_path)
        self.assertFalse(gate.approve("nonexistent"))
        self.assertIsNone(gate.get("nonexistent"))

    def test_count_by_state(self):
        from core.learn.ascension_gate import AscensionGate, PatternCandidate
        gate = AscensionGate(path=self.candidates_path)
        gate.add(PatternCandidate(summary="a", intent="TEST"))
        c2 = PatternCandidate(summary="b", intent="TEST")
        cid2 = gate.add(c2)
        gate.approve(cid2)

        counts = gate.count_by_state()
        self.assertEqual(counts["QUARANTINED"], 1)
        self.assertEqual(counts["APPROVED"], 1)
        self.assertEqual(counts["BANNED"], 0)

    def test_approved_patterns(self):
        from core.learn.ascension_gate import AscensionGate, PatternCandidate
        gate = AscensionGate(path=self.candidates_path)
        c1 = PatternCandidate(summary="a", intent="TEST")
        cid1 = gate.add(c1)
        gate.add(PatternCandidate(summary="b", intent="TEST"))
        gate.approve(cid1)

        approved = gate.approved_patterns()
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0]["id"], cid1)


# ===================================================================
# Ingestion (structure extraction)
# ===================================================================

class TestIngestionStructure(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_extract_structure(self):
        from core.learn.ingestion import extract_structure
        py_file = os.path.join(self.tmpdir, "sample.py")
        with open(py_file, "w") as f:
            f.write('"""Module docstring."""\n\nimport os\nimport sys\n\n'
                    'class Foo:\n    """Foo class."""\n    pass\n\n'
                    'def bar():\n    """Bar function."""\n    return 42\n')

        fs = extract_structure(py_file)
        self.assertIsNotNone(fs)
        self.assertIn("os", fs.imports)
        self.assertIn("sys", fs.imports)
        self.assertIn("Foo", fs.classes)
        self.assertIn("bar", fs.functions)
        self.assertTrue(len(fs.docstrings) >= 1)
        self.assertIn("Foo", fs.symbols)
        self.assertIn("bar", fs.symbols)

    def test_extract_structure_syntax_error(self):
        from core.learn.ingestion import extract_structure
        py_file = os.path.join(self.tmpdir, "broken.py")
        with open(py_file, "w") as f:
            f.write("def broken(\n")  # syntax error
        fs = extract_structure(py_file)
        self.assertIsNotNone(fs)  # should still return partial

    def test_extract_structure_missing_file(self):
        from core.learn.ingestion import extract_structure
        fs = extract_structure("/nonexistent/file.py")
        self.assertIsNone(fs)


# ===================================================================
# Semantic Index (mocked embeddings)
# ===================================================================

class TestSemanticIndex(unittest.TestCase):
    """Test semantic index with mocked embedding model."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.index_path = os.path.join(self.tmpdir, "test.faiss")
        self.meta_path = os.path.join(self.tmpdir, "test_meta.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_meta_persistence(self):
        """Test metadata JSONL read/write without requiring faiss/transformers."""
        from core.learn.semantic import SemanticRecord
        rec = SemanticRecord(
            id="abc123",
            summary="Test record",
            file_path="test.py",
            symbols=["foo", "bar"],
            intent="TEST",
            content_hash="hash123",
        )
        # Write
        with open(self.meta_path, "w") as f:
            f.write(json.dumps(rec.to_dict()) + "\n")

        # Read back
        with open(self.meta_path) as f:
            data = json.loads(f.readline())

        self.assertEqual(data["id"], "abc123")
        self.assertEqual(data["summary"], "Test record")
        self.assertEqual(data["symbols"], ["foo", "bar"])


# ===================================================================
# Constitution
# ===================================================================

class TestConstitution(unittest.TestCase):
    """Test the immutable Constitution and its enforcement."""

    def test_constitution_is_immutable_tuple(self):
        from core.learn.constitution import CONSTITUTION
        self.assertIsInstance(CONSTITUTION, tuple)
        self.assertEqual(len(CONSTITUTION), 10)

    def test_constitution_cannot_be_mutated(self):
        from core.learn.constitution import CONSTITUTION
        with self.assertRaises(TypeError):
            CONSTITUTION[0] = "tampered"  # type: ignore

    def test_enforcer_validates_immutability(self):
        from core.learn.constitution import ConstitutionEnforcer, CCTracker
        tmpdir = tempfile.mkdtemp()
        tracker = CCTracker(path=os.path.join(tmpdir, "cc.jsonl"))
        enforcer = ConstitutionEnforcer(cc_tracker=tracker)
        self.assertTrue(enforcer.validate_immutability())
        shutil.rmtree(tmpdir, ignore_errors=True)


class TestCCTracker(unittest.TestCase):
    """Test Cognitive Consistency tracking."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cc_path = os.path.join(self.tmpdir, "cc.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_first_observation_sets_cc(self):
        from core.learn.constitution import CCTracker
        tracker = CCTracker(path=self.cc_path)
        entry = tracker.update("fp1", observation_score=0.8, domain="core")
        self.assertAlmostEqual(entry.cc_score, 0.8)
        self.assertEqual(entry.observations, 1)
        self.assertEqual(entry.domain, "core")

    def test_ema_update(self):
        from core.learn.constitution import CCTracker, CC_DECAY_ALPHA
        tracker = CCTracker(path=self.cc_path)
        tracker.update("fp1", observation_score=0.8)
        entry = tracker.update("fp1", observation_score=0.6)
        expected = CC_DECAY_ALPHA * 0.6 + (1 - CC_DECAY_ALPHA) * 0.8
        self.assertAlmostEqual(entry.cc_score, expected, places=3)
        self.assertEqual(entry.observations, 2)

    def test_dead_end_detection(self):
        from core.learn.constitution import CCTracker, CC_DEAD_END_MIN_OBS
        tracker = CCTracker(path=self.cc_path)
        # Feed low-confidence observations to trigger dead-end
        for _ in range(CC_DEAD_END_MIN_OBS + 1):
            tracker.update("fp_bad", observation_score=0.05)
        self.assertTrue(tracker.is_dead_end("fp_bad"))

    def test_no_false_dead_end(self):
        from core.learn.constitution import CCTracker
        tracker = CCTracker(path=self.cc_path)
        tracker.update("fp_good", observation_score=0.9)
        tracker.update("fp_good", observation_score=0.85)
        self.assertFalse(tracker.is_dead_end("fp_good"))

    def test_get_cc_unseen(self):
        from core.learn.constitution import CCTracker
        tracker = CCTracker(path=self.cc_path)
        self.assertEqual(tracker.get_cc("nonexistent"), 0.0)

    def test_stats(self):
        from core.learn.constitution import CCTracker
        tracker = CCTracker(path=self.cc_path)
        tracker.update("fp1", observation_score=0.7, domain="core")
        tracker.update("fp2", observation_score=0.3, domain="cli")
        stats = tracker.stats()
        self.assertEqual(stats["total"], 2)
        self.assertIn("avg_cc", stats)
        self.assertIn("by_domain", stats)


class TestCCModeDrivation(unittest.TestCase):
    """Test Constitution rules 1, 2, 6, 7, 8, 9, 10."""

    def test_rule_1_default_observe(self):
        from core.learn.constitution import derive_mode_from_cc
        mode = derive_mode_from_cc(cc_score=0.0, sensitivity="CLEAN", impact="low")
        self.assertEqual(mode, "OBSERVE")

    def test_rule_6_cc_low_observe(self):
        from core.learn.constitution import derive_mode_from_cc
        mode = derive_mode_from_cc(cc_score=0.3, sensitivity="CLEAN", impact="medium")
        self.assertEqual(mode, "OBSERVE")

    def test_rule_7_cc_medium_propose(self):
        from core.learn.constitution import derive_mode_from_cc
        mode = derive_mode_from_cc(cc_score=0.5, sensitivity="CLEAN", impact="medium")
        self.assertEqual(mode, "PROPOSE")

    def test_rule_8_cc_high_propose_proceed_whitelisted(self):
        from core.learn.constitution import derive_mode_from_cc
        mode = derive_mode_from_cc(
            cc_score=0.9, sensitivity="CLEAN", impact="low",
            is_whitelisted=True,
        )
        self.assertEqual(mode, "PROPOSE_PROCEED")

    def test_rule_8_cc_high_not_whitelisted_propose(self):
        from core.learn.constitution import derive_mode_from_cc
        mode = derive_mode_from_cc(
            cc_score=0.9, sensitivity="CLEAN", impact="low",
            is_whitelisted=False,
        )
        self.assertEqual(mode, "PROPOSE")

    def test_rule_9_sensitive_always_observe(self):
        from core.learn.constitution import derive_mode_from_cc
        for sens in ("PII", "SECRETS"):
            mode = derive_mode_from_cc(cc_score=0.95, sensitivity=sens, impact="low")
            self.assertEqual(mode, "OBSERVE", f"Sensitive={sens} should be OBSERVE")

    def test_rule_9_high_impact_observe(self):
        from core.learn.constitution import derive_mode_from_cc
        mode = derive_mode_from_cc(cc_score=0.95, sensitivity="CLEAN", impact="high")
        self.assertEqual(mode, "OBSERVE")

    def test_rule_10_dead_end_observe(self):
        from core.learn.constitution import derive_mode_from_cc
        mode = derive_mode_from_cc(
            cc_score=0.9, sensitivity="CLEAN", impact="low",
            is_dead_end=True,
        )
        self.assertEqual(mode, "OBSERVE")


class TestConstitutionEnforcer(unittest.TestCase):
    """Test the full enforcer pipeline."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cc_path = os.path.join(self.tmpdir, "cc.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_enforce_first_observation(self):
        from core.learn.constitution import ConstitutionEnforcer, CCTracker
        tracker = CCTracker(path=self.cc_path)
        enforcer = ConstitutionEnforcer(cc_tracker=tracker)
        verdict = enforcer.enforce(
            fingerprint="fp1",
            sensitivity="CLEAN",
            impact="low",
            observation_score=0.3,
            domain="core",
        )
        self.assertTrue(verdict["allowed"])
        self.assertEqual(verdict["mode"], "OBSERVE")  # first obs, cc=0.3 < 0.4
        self.assertEqual(verdict["domain"], "core")

    def test_enforce_blocks_execute_request(self):
        """Rule 2: never execute because requested."""
        from core.learn.constitution import ConstitutionEnforcer, CCTracker
        tracker = CCTracker(path=self.cc_path)
        enforcer = ConstitutionEnforcer(cc_tracker=tracker)
        verdict = enforcer.enforce(
            fingerprint="fp1",
            sensitivity="CLEAN",
            impact="low",
            observation_score=0.9,
            requested_mode="EXECUTE",
        )
        self.assertFalse(verdict["allowed"])
        self.assertEqual(verdict["mode"], "OBSERVE")
        self.assertIn(2, verdict["violations"])

    def test_enforce_warns_sensitive(self):
        """Rule 3: warn on sensitive data (scrubber handles actual redaction)."""
        from core.learn.constitution import ConstitutionEnforcer, CCTracker
        tracker = CCTracker(path=self.cc_path)
        enforcer = ConstitutionEnforcer(cc_tracker=tracker)
        verdict = enforcer.enforce(
            fingerprint="fp1",
            sensitivity="SECRETS",
            impact="low",
            observation_score=0.9,
        )
        self.assertIn(3, verdict["violations"])
        # Still allowed (scrubber handles redaction, rule 3 is a warning)
        self.assertTrue(verdict["allowed"])
        self.assertEqual(verdict["mode"], "OBSERVE")  # sensitive → OBSERVE


class TestDomainClassifier(unittest.TestCase):

    def test_core_domain(self):
        from core.learn.constitution import classify_domain
        self.assertEqual(classify_domain("core/policy_router.py"), "core")

    def test_pipeline_domain(self):
        from core.learn.constitution import classify_domain
        self.assertEqual(classify_domain("pipeline/signals.py"), "pipeline")

    def test_tests_domain(self):
        from core.learn.constitution import classify_domain
        self.assertEqual(classify_domain("tests/test_learn.py"), "tests")

    def test_cli_domain(self):
        from core.learn.constitution import classify_domain
        self.assertEqual(classify_domain("cli/vx_main.py"), "cli")

    def test_services_domain(self):
        from core.learn.constitution import classify_domain
        self.assertEqual(classify_domain("services/core/app.py"), "services")

    def test_infra_domain(self):
        from core.learn.constitution import classify_domain
        self.assertEqual(classify_domain("setup.py"), "infra")

    def test_unknown_domain(self):
        from core.learn.constitution import classify_domain
        self.assertEqual(classify_domain("random_file.py"), "unknown")


if __name__ == "__main__":
    unittest.main()
