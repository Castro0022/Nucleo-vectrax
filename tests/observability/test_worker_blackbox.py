"""
Tests for core/observability/worker_blackbox.py

Covers:
  - Diagnosis engine: all 8 cause detection rules
  - Positive/negative evidence generation
  - Confidence adjustment by evidence balance
  - Feedback submission and stats aggregation
  - Incident persistence and retrieval
"""

import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

# Patch the incidents file to a temp location before importing
_TEMP_DIR = tempfile.mkdtemp()
_TEMP_INCIDENTS = os.path.join(_TEMP_DIR, "test_incidents.jsonl")

import core.observability.worker_blackbox as bb

# Override persistence path
bb._INCIDENTS_FILE = _TEMP_INCIDENTS
bb._VAULT = _TEMP_DIR


def _make_incident(**overrides):
    """Build a minimal incident dict for testing."""
    base = {
        "incident_id": f"INC-TEST-{int(time.time())}",
        "timestamp": "2026-06-05T21:00:00+00:00",
        "worker_name": "pipeline_worker",
        "worker_pid": 999,
        "trigger": "test",
        "heartbeat_age_s": 35.0,
        "last_logs": [],
        "active_task": {"status": "no_active_task"},
        "resources": {"ram_mb": 45, "threads": 2, "state": "S"},
        "queue": {"pending": 0, "processing": 0, "done": 0, "error": 0},
        "recent_errors": [],
        "external_calls": {"pending_apis": [], "pending_count": 0},
        "traceback": "",
    }
    base.update(overrides)
    return base


class TestDiagnosisCauses(unittest.TestCase):
    """Each rule should set the correct causa_probable."""

    def setUp(self):
        # Clear incidents file between tests
        if os.path.exists(_TEMP_INCIDENTS):
            os.unlink(_TEMP_INCIDENTS)

    @patch.object(bb, '_alert_creator')
    def test_tarea_bloqueada(self, mock_alert):
        inc = _make_incident(active_task={"duration_s": 25, "msg_id": "abc"})
        diag = bb.diagnose_incident(inc)
        self.assertEqual(diag["causa_probable"], "tarea_bloqueada")
        # Confidence starts at 0.8 but negative evidence penalizes it
        self.assertGreaterEqual(diag["confianza"], 0.4)
        self.assertIn("Tarea activa", diag["evidencia_positiva"][0])

    @patch.object(bb, '_alert_creator')
    def test_timeout_externo(self, mock_alert):
        inc = _make_incident(
            external_calls={"pending_apis": ["OpenAI"], "pending_count": 1},
        )
        diag = bb.diagnose_incident(inc)
        self.assertEqual(diag["causa_probable"], "timeout_externo")

    @patch.object(bb, '_alert_creator')
    def test_gateway_stale_without_errors_not_timeout_externo(self, mock_alert):
        """Regression: a frozen gateway (stale HB) whose logs only contain
        benign 'telegram' lines must NOT be diagnosed as timeout_externo.
        This was the recurring false positive.
        """
        logs = [
            "POLL #5 | 0 updates | 30000ms",
            "api.telegram.org getUpdates ok",
            "STATUS | up=1h0m0s | processed=10",
        ]
        ext = bb._detect_external_calls(logs)
        inc = _make_incident(
            worker_name="telegram_gateway",
            heartbeat_age_s=64,
            external_calls=ext,
            last_logs=logs,
        )
        diag = bb.diagnose_incident(inc)
        self.assertNotEqual(diag["causa_probable"], "timeout_externo")

    @patch.object(bb, '_alert_creator')
    def test_memoria_alta(self, mock_alert):
        # Umbral real de memoria_alta es >1000MB (Python+ML arranca en ~850MB,
        # que es rango NORMAL y no debe marcarse como alto). 1100MB sí lo supera.
        inc = _make_incident(resources={"ram_mb": 1100, "threads": 5})
        diag = bb.diagnose_incident(inc)
        self.assertEqual(diag["causa_probable"], "memoria_alta")

    @patch.object(bb, '_alert_creator')
    def test_error_db(self, mock_alert):
        inc = _make_incident(
            recent_errors=["sqlite3.OperationalError: database is locked"],
        )
        diag = bb.diagnose_incident(inc)
        self.assertEqual(diag["causa_probable"], "error_db")

    @patch.object(bb, '_alert_creator')
    def test_cola_saturada(self, mock_alert):
        inc = _make_incident(queue={"pending": 15, "processing": 3})
        diag = bb.diagnose_incident(inc)
        self.assertEqual(diag["causa_probable"], "cola_saturada")

    @patch.object(bb, '_alert_creator')
    def test_fallo_red(self, mock_alert):
        inc = _make_incident(
            recent_errors=["ConnectionError: network unreachable"],
        )
        diag = bb.diagnose_incident(inc)
        self.assertEqual(diag["causa_probable"], "fallo_red")

    @patch.object(bb, '_alert_creator')
    def test_excepcion_no_capturada(self, mock_alert):
        inc = _make_incident(traceback="Traceback (most recent call last):\n  File x\nValueError: bad")
        diag = bb.diagnose_incident(inc)
        self.assertEqual(diag["causa_probable"], "excepcion_no_capturada")

    @patch.object(bb, '_alert_creator')
    def test_loop_infinito(self, mock_alert):
        inc = _make_incident(heartbeat_age_s=90)
        diag = bb.diagnose_incident(inc)
        self.assertEqual(diag["causa_probable"], "loop_infinito")

    @patch.object(bb, '_alert_creator')
    def test_proceso_muerto(self, mock_alert):
        """No indicators → proceso_muerto with low confidence."""
        inc = _make_incident()
        diag = bb.diagnose_incident(inc)
        self.assertEqual(diag["causa_probable"], "proceso_muerto")
        self.assertLessEqual(diag["confianza"], 0.3)


class TestEvidenceBalance(unittest.TestCase):
    """Positive and negative evidence must be tracked and affect confidence."""

    def setUp(self):
        if os.path.exists(_TEMP_INCIDENTS):
            os.unlink(_TEMP_INCIDENTS)

    @patch.object(bb, '_alert_creator')
    def test_has_both_evidence_types(self, mock_alert):
        inc = _make_incident(
            external_calls={"pending_apis": ["OpenAI"], "pending_count": 1},
        )
        diag = bb.diagnose_incident(inc)
        self.assertIn("evidencia_positiva", diag)
        self.assertIn("evidencia_negativa", diag)
        self.assertGreater(len(diag["evidencia_positiva"]), 0)
        self.assertGreater(len(diag["evidencia_negativa"]), 0)

    @patch.object(bb, '_alert_creator')
    def test_negative_evidence_lowers_confidence(self, mock_alert):
        """Many negative signals should reduce confidence."""
        # timeout_externo with lots of negative evidence
        inc = _make_incident(
            external_calls={"pending_apis": ["OpenAI"], "pending_count": 1},
            resources={"ram_mb": 30},  # very low RAM → negative
        )
        diag = bb.diagnose_incident(inc)
        # With 1 positive and 5+ negative, confidence should be < 0.75
        self.assertLess(diag["confianza"], 0.75)

    @patch.object(bb, '_alert_creator')
    def test_multiple_positive_boosts_confidence(self, mock_alert):
        """Task blocked + timeout + traceback = high confidence."""
        inc = _make_incident(
            active_task={"duration_s": 30, "msg_id": "x"},
            external_calls={"pending_apis": ["OpenAI"], "pending_count": 1},
            traceback="Traceback...\nTimeoutError",
            recent_errors=["timeout in openai call"],
        )
        diag = bb.diagnose_incident(inc)
        self.assertGreaterEqual(diag["confianza"], 0.85)

    @patch.object(bb, '_alert_creator')
    def test_clean_system_produces_all_negative(self, mock_alert):
        """A healthy system snapshot should have mostly negative evidence."""
        inc = _make_incident(resources={"ram_mb": 30})
        diag = bb.diagnose_incident(inc)
        n_neg = len(diag.get("evidencia_negativa", []))
        n_pos = len(diag.get("evidencia_positiva", []))
        self.assertGreater(n_neg, n_pos)

    @patch.object(bb, '_alert_creator')
    def test_backward_compat_evidencia_field(self, mock_alert):
        """The 'evidencia' field should still exist for backward compat."""
        inc = _make_incident()
        diag = bb.diagnose_incident(inc)
        self.assertIn("evidencia", diag)
        self.assertEqual(diag["evidencia"], diag["evidencia_positiva"])


class TestFeedback(unittest.TestCase):
    """Feedback submission and stats tracking."""

    def setUp(self):
        if os.path.exists(_TEMP_INCIDENTS):
            os.unlink(_TEMP_INCIDENTS)

    def test_submit_confirmed(self):
        fb = bb.submit_feedback("INC-TEST-001", "confirmed", notes="Looks correct")
        self.assertEqual(fb["verdict"], "confirmed")
        self.assertEqual(fb["incident_id"], "INC-TEST-001")
        self.assertIn("timestamp", fb)

    def test_submit_rejected_with_cause(self):
        fb = bb.submit_feedback("INC-TEST-002", "rejected", creator_cause="fallo_red")
        self.assertEqual(fb["verdict"], "rejected")
        self.assertEqual(fb["creator_cause"], "fallo_red")

    def test_submit_partial(self):
        fb = bb.submit_feedback("INC-TEST-003", "partial", notes="Partly right")
        self.assertEqual(fb["verdict"], "partial")

    def test_feedback_persisted(self):
        bb.submit_feedback("INC-PERSIST", "confirmed")
        incidents = bb.get_incidents(limit=10)
        feedbacks = [i for i in incidents if i.get("type") == "feedback"]
        self.assertGreater(len(feedbacks), 0)
        self.assertEqual(feedbacks[-1]["incident_id"], "INC-PERSIST")

    @patch.object(bb, '_alert_creator')
    def test_feedback_stats(self, mock_alert):
        # Create a diagnosis + feedback pair
        inc = _make_incident(incident_id="INC-STATS-1")
        bb.diagnose_incident(inc)
        bb.submit_feedback("INC-STATS-1", "confirmed")

        inc2 = _make_incident(incident_id="INC-STATS-2")
        bb.diagnose_incident(inc2)
        bb.submit_feedback("INC-STATS-2", "rejected", creator_cause="fallo_red")

        stats = bb.get_feedback_stats()
        self.assertEqual(stats["confirmed"], 1)
        self.assertEqual(stats["rejected"], 1)
        self.assertEqual(stats["total_feedback"], 2)
        self.assertEqual(stats["accuracy_pct"], 50.0)


class TestPersistence(unittest.TestCase):
    """Incident file read/write."""

    def setUp(self):
        if os.path.exists(_TEMP_INCIDENTS):
            os.unlink(_TEMP_INCIDENTS)

    @patch.object(bb, '_alert_creator')
    def test_incident_saved_and_loaded(self, mock_alert):
        inc = _make_incident(incident_id="INC-SAVE-TEST")
        bb._save_incident(inc)
        loaded = bb.get_incidents(limit=10)
        ids = [i.get("incident_id") for i in loaded]
        self.assertIn("INC-SAVE-TEST", ids)

    @patch.object(bb, '_alert_creator')
    def test_max_retention(self, mock_alert):
        """Should not exceed _MAX_INCIDENTS."""
        old_max = bb._MAX_INCIDENTS
        bb._MAX_INCIDENTS = 5
        try:
            for i in range(10):
                bb._save_incident({"test_id": i})
            loaded = bb.get_incidents(limit=100)
            self.assertLessEqual(len(loaded), 5)
        finally:
            bb._MAX_INCIDENTS = old_max

    def test_empty_file_returns_empty(self):
        self.assertEqual(bb.get_incidents(), [])


class TestDataCollectors(unittest.TestCase):
    """Log parsing, error extraction, external call detection."""

    def test_extract_errors(self):
        logs = [
            "2026-06-05 INFO normal line",
            "2026-06-05 ERROR something broke",
            "2026-06-05 INFO another normal",
            "2026-06-05 CRITICAL database down",
            "2026-06-05 INFO fine",
        ]
        errors = bb._extract_errors(logs, 10)
        self.assertEqual(len(errors), 2)
        self.assertIn("ERROR", errors[0])
        self.assertIn("CRITICAL", errors[1])

    def test_detect_external_calls(self):
        logs = [
            "calling api.openai.com/v1/chat",      # mention only -> NOT counted
            "timeout from etoro client",           # etoro + timeout -> counted
            "normal processing line",
            "api.telegram.org response ok",        # mention only -> NOT counted
        ]
        calls = bb._detect_external_calls(logs)
        # Only providers with REAL problem evidence are counted as pending.
        self.assertIn("eToro", calls["pending_apis"])
        self.assertIn("timeout_detected", calls["pending_apis"])
        self.assertNotIn("OpenAI", calls["pending_apis"])
        self.assertNotIn("Telegram", calls["pending_apis"])
        # Mere mentions are still surfaced as 'seen' for context.
        self.assertIn("OpenAI", calls["seen_apis"])
        self.assertIn("Telegram", calls["seen_apis"])

    def test_provider_mention_without_error_is_not_pending(self):
        """Regression: provider name alone (no error indicator) must not be
        counted as a pending/timed-out external call."""
        logs = [
            "POLL #5 | 0 updates | 30000ms",
            "api.telegram.org getUpdates ok",
            "STATUS | up=1h0m0s | processed=10",
        ]
        calls = bb._detect_external_calls(logs)
        self.assertEqual(calls["pending_count"], 0)
        self.assertNotIn("Telegram", calls["pending_apis"])

    def test_detect_no_external_calls(self):
        logs = ["just a normal log line", "nothing special here"]
        calls = bb._detect_external_calls(logs)
        self.assertEqual(calls["pending_count"], 0)

    def test_extract_traceback(self):
        logs = [
            "normal line",
            "Traceback (most recent call last):",
            '  File "worker.py", line 42, in process',
            "    result = do_thing()",
            "ValueError: invalid input",
            "normal after",
        ]
        tb = bb._extract_traceback(logs)
        self.assertIn("Traceback", tb)
        self.assertIn("ValueError", tb)

    def test_extract_no_traceback(self):
        logs = ["normal line 1", "normal line 2"]
        tb = bb._extract_traceback(logs)
        self.assertEqual(tb, "")

    def test_get_queue_state_missing_db(self):
        """Should return zeros if DB doesn't exist."""
        old_vault = bb._VAULT
        bb._VAULT = "/nonexistent/path"
        try:
            q = bb._get_queue_state()
            self.assertEqual(q["pending"], 0)
        finally:
            bb._VAULT = old_vault


if __name__ == "__main__":
    unittest.main()
