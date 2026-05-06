"""
tests/sovereignty/test_sovereignty.py

Exhaustive unit tests for the User Sovereignty Engine.
Covers policy, consent, ledger, authorizer, decorator and mode behavior.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

# IMPORTANT: redirect storage paths BEFORE importing the modules so
# tests don't touch the user's real ~/.vectrax/* state.
_TMPDIR = tempfile.mkdtemp(prefix="vx_sovereignty_test_")
os.environ["VECTRAX_SOVEREIGNTY_DB"] = os.path.join(_TMPDIR, "sov.db")
os.environ["VECTRAX_SOVEREIGNTY_LEDGER"] = os.path.join(_TMPDIR, "sov.jsonl")

from core.sovereignty import (  # noqa: E402
    SendReason,
    ConsentType,
    DecisionStatus,
    grant_consent,
    revoke_consent,
    has_consent,
    list_consents,
    require_authorization,
    sovereignty_mode,
    audit_stats,
    recent_decisions,
    authorized_send,
)
from core.sovereignty.consent import reset_for_tests  # noqa: E402


CREATOR = "2030762343"


def _wipe_ledger():
    p = os.environ["VECTRAX_SOVEREIGNTY_LEDGER"]
    try:
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        pass


class TestPolicy(unittest.TestCase):
    def test_always_allowed_set_contents(self):
        from core.sovereignty.policy import ALWAYS_ALLOWED_REASONS
        self.assertIn(SendReason.USER_REPLY, ALWAYS_ALLOWED_REASONS)
        self.assertIn(SendReason.USER_ACK, ALWAYS_ALLOWED_REASONS)
        self.assertIn(SendReason.USER_COMMAND, ALWAYS_ALLOWED_REASONS)
        self.assertIn(SendReason.ERROR_TO_USER, ALWAYS_ALLOWED_REASONS)
        self.assertNotIn(
            SendReason.PROACTIVE_INSIGHT, ALWAYS_ALLOWED_REASONS,
        )

    def test_consent_for_reason(self):
        from core.sovereignty.policy import consent_for_reason
        self.assertEqual(
            consent_for_reason(SendReason.PROACTIVE_INSIGHT),
            ConsentType.PROACTIVE,
        )
        self.assertEqual(
            consent_for_reason(SendReason.SCHEDULED_REMINDER),
            ConsentType.REMINDERS,
        )
        self.assertIsNone(consent_for_reason(SendReason.USER_REPLY))


class TestConsent(unittest.TestCase):
    def setUp(self):
        reset_for_tests()

    def test_grant_and_check(self):
        self.assertTrue(grant_consent("tg:1", ConsentType.PROACTIVE))
        self.assertTrue(has_consent("tg:1", ConsentType.PROACTIVE))
        self.assertFalse(has_consent("tg:1", ConsentType.REMINDERS))
        self.assertFalse(has_consent("tg:2", ConsentType.PROACTIVE))

    def test_revoke(self):
        grant_consent("tg:1", ConsentType.PROACTIVE)
        self.assertTrue(revoke_consent("tg:1", ConsentType.PROACTIVE))
        self.assertFalse(has_consent("tg:1", ConsentType.PROACTIVE))

    def test_list_consents(self):
        grant_consent("tg:1", ConsentType.PROACTIVE)
        grant_consent("tg:1", ConsentType.REMINDERS)
        consents = list_consents("tg:1")
        self.assertIn(ConsentType.PROACTIVE, consents)
        self.assertIn(ConsentType.REMINDERS, consents)


class TestAuthorizerEnforce(unittest.TestCase):
    """Modo enforce: bloqueo real."""

    def setUp(self):
        reset_for_tests()
        _wipe_ledger()
        self._env = mock.patch.dict(
            os.environ, {"SOVEREIGNTY_MODE": "enforce"}
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_user_reply_always_allowed(self):
        d = require_authorization(123, SendReason.USER_REPLY)
        self.assertEqual(d.status, DecisionStatus.ALLOWED)
        self.assertEqual(d.rule, "always_allowed")
        self.assertTrue(d.is_allowed)

    def test_proactive_blocked_without_consent(self):
        d = require_authorization(123, SendReason.PROACTIVE_INSIGHT)
        self.assertEqual(d.status, DecisionStatus.BLOCKED)
        self.assertEqual(d.rule, "no_consent")
        self.assertFalse(d.is_allowed)

    def test_proactive_allowed_with_consent(self):
        grant_consent("tg:123", ConsentType.PROACTIVE)
        d = require_authorization(123, SendReason.PROACTIVE_INSIGHT)
        self.assertEqual(d.status, DecisionStatus.ALLOWED)
        self.assertEqual(d.rule, "consent_granted")

    def test_admin_broadcast_creator_only(self):
        d_creator = require_authorization(
            int(CREATOR), SendReason.ADMIN_BROADCAST,
        )
        self.assertEqual(d_creator.status, DecisionStatus.ALLOWED)
        d_other = require_authorization(999, SendReason.ADMIN_BROADCAST)
        self.assertEqual(d_other.status, DecisionStatus.BLOCKED)
        self.assertEqual(d_other.rule, "admin_check_failed")

    def test_undeclared_blocked(self):
        d = require_authorization(123, SendReason.UNDECLARED)
        self.assertEqual(d.status, DecisionStatus.BLOCKED)
        self.assertEqual(d.rule, "undeclared")

    def test_reminder_requires_reminders_consent(self):
        grant_consent("tg:777", ConsentType.PROACTIVE)
        d = require_authorization(777, SendReason.SCHEDULED_REMINDER)
        # tiene PROACTIVE pero no REMINDERS
        self.assertEqual(d.status, DecisionStatus.BLOCKED)
        grant_consent("tg:777", ConsentType.REMINDERS)
        d = require_authorization(777, SendReason.SCHEDULED_REMINDER)
        self.assertEqual(d.status, DecisionStatus.ALLOWED)


class TestAuthorizerObserve(unittest.TestCase):
    """Modo observe: NO bloquea, solo loggea."""

    def setUp(self):
        reset_for_tests()
        _wipe_ledger()
        self._env = mock.patch.dict(
            os.environ, {"SOVEREIGNTY_MODE": "observe"}
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_observe_does_not_block(self):
        d = require_authorization(123, SendReason.PROACTIVE_INSIGHT)
        self.assertEqual(d.status, DecisionStatus.OBSERVED)
        self.assertTrue(d.is_allowed)  # OBSERVED counts as allowed
        self.assertEqual(d.rule, "no_consent")

    def test_observe_records_in_ledger(self):
        require_authorization(123, SendReason.PROACTIVE_INSIGHT)
        require_authorization(123, SendReason.USER_REPLY)
        recent = recent_decisions(10)
        statuses = {r["status"] for r in recent}
        self.assertIn("OBSERVED", statuses)
        self.assertIn("ALLOWED", statuses)


class TestAuthorizerOff(unittest.TestCase):
    """Modo off: bypass completo."""

    def setUp(self):
        reset_for_tests()
        _wipe_ledger()
        self._env = mock.patch.dict(
            os.environ, {"SOVEREIGNTY_MODE": "off"}
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_off_allows_everything(self):
        d = require_authorization(123, SendReason.PROACTIVE_INSIGHT)
        self.assertEqual(d.status, DecisionStatus.ALLOWED)
        self.assertEqual(d.rule, "bypass")


class TestSovereigntyMode(unittest.TestCase):
    def test_default_mode_is_observe(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SOVEREIGNTY_MODE", None)
            self.assertEqual(sovereignty_mode(), "observe")

    def test_invalid_mode_falls_back_to_observe(self):
        with mock.patch.dict(
            os.environ, {"SOVEREIGNTY_MODE": "garbage"}
        ):
            self.assertEqual(sovereignty_mode(), "observe")

    def test_enforce_mode(self):
        with mock.patch.dict(
            os.environ, {"SOVEREIGNTY_MODE": "enforce"}
        ):
            self.assertEqual(sovereignty_mode(), "enforce")


class TestLedger(unittest.TestCase):
    def setUp(self):
        reset_for_tests()
        _wipe_ledger()
        self._env = mock.patch.dict(
            os.environ, {"SOVEREIGNTY_MODE": "enforce"}
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_audit_stats_aggregates(self):
        require_authorization(1, SendReason.USER_REPLY)
        require_authorization(2, SendReason.USER_REPLY)
        require_authorization(3, SendReason.PROACTIVE_INSIGHT)
        stats = audit_stats(window_seconds=3600)
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["by_status"].get("ALLOWED"), 2)
        self.assertEqual(stats["by_status"].get("BLOCKED"), 1)
        self.assertEqual(
            stats["blocked_by_reason"].get("proactive_insight"), 1,
        )


class TestDecorator(unittest.TestCase):
    def setUp(self):
        reset_for_tests()
        _wipe_ledger()

    def test_decorator_allows_user_reply(self):
        with mock.patch.dict(
            os.environ, {"SOVEREIGNTY_MODE": "enforce"}
        ):
            calls = []

            @authorized_send(SendReason.USER_REPLY)
            def fake_send(chat_id, text):
                calls.append((chat_id, text))
                return True

            ok = fake_send(123, "hola")
            self.assertTrue(ok)
            self.assertEqual(len(calls), 1)

    def test_decorator_blocks_proactive_in_enforce(self):
        with mock.patch.dict(
            os.environ, {"SOVEREIGNTY_MODE": "enforce"}
        ):
            calls = []

            @authorized_send(SendReason.PROACTIVE_INSIGHT)
            def fake_send(chat_id, text):
                calls.append((chat_id, text))
                return True

            res = fake_send(123, "follow-up no pedido")
            self.assertFalse(res)
            self.assertEqual(calls, [])

    def test_decorator_observe_does_not_block(self):
        with mock.patch.dict(
            os.environ, {"SOVEREIGNTY_MODE": "observe"}
        ):
            calls = []

            @authorized_send(SendReason.PROACTIVE_INSIGHT)
            def fake_send(chat_id, text):
                calls.append((chat_id, text))
                return True

            res = fake_send(123, "follow-up")
            # observe permite, pero registra OBSERVED
            self.assertTrue(res)
            self.assertEqual(len(calls), 1)

    def test_decorator_reason_override(self):
        with mock.patch.dict(
            os.environ, {"SOVEREIGNTY_MODE": "enforce"}
        ):
            calls = []

            @authorized_send(SendReason.PROACTIVE_INSIGHT)
            def fake_send(chat_id, text, **kw):
                calls.append((chat_id, text))
                return True

            # override a USER_REPLY → debe pasar
            res = fake_send(
                123, "hola",
                _sovereignty_reason=SendReason.USER_REPLY,
            )
            self.assertTrue(res)
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
