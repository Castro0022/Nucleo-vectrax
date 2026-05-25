"""
tests/tiers/test_daily_limit.py — Daily message limit enforcement.

Covers:
  1. Only user messages increment the counter (not bot responses)
  2. At limit: sends notification with Stripe link once
  3. After notification: subsequent messages are silently ignored (reason="")
  4. Counter resets at midnight
  5. Creator is exempt
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_TMPDIR = tempfile.mkdtemp(prefix="vx_tier_test_")
_TEST_DB = os.path.join(_TMPDIR, "user_memory.db")

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Point user_tiers to temp DB
import core.operator.user_tiers as ut
ut._DB_PATH = _TEST_DB


class TestDailyLimit(unittest.TestCase):

    def setUp(self):
        """Fresh DB for each test."""
        if os.path.exists(_TEST_DB):
            os.unlink(_TEST_DB)

    def test_20_messages_then_blocked(self):
        """User sends 20 messages → allowed. Message 21 → blocked with reason."""
        user = "tg:test_limit_user"

        # Send 20 messages — all allowed
        for i in range(20):
            access = ut.check_access(user)
            self.assertTrue(access.allowed, f"Message {i+1} should be allowed")

        # Message 21 — blocked with notification
        access = ut.check_access(user)
        self.assertFalse(access.allowed)
        self.assertIn("límite diario de 20 mensajes", access.reason)
        self.assertEqual(access.msgs_today, 20)

    def test_limit_notification_sent_once(self):
        """After limit, first rejection has reason. Subsequent ones are empty (silent)."""
        user = "tg:test_notify_once"

        # Exhaust limit
        for _ in range(20):
            ut.check_access(user)

        # First rejection — has message
        access1 = ut.check_access(user)
        self.assertFalse(access1.allowed)
        self.assertTrue(len(access1.reason) > 0, "First rejection should have message")

        # Second rejection — silent (already notified)
        access2 = ut.check_access(user)
        self.assertFalse(access2.allowed)
        self.assertEqual(access2.reason, "", "Second rejection should be silent")

    def test_stripe_link_in_message(self):
        """Limit message should contain Stripe checkout URL or /upgrade fallback."""
        user = "tg:test_stripe_link"

        for _ in range(20):
            ut.check_access(user)

        # Mock Stripe to return a URL
        with patch("services.billing.stripe_billing.create_checkout_session",
                    return_value="https://checkout.stripe.com/test_session"):
            access = ut.check_access(user)
            self.assertFalse(access.allowed)
            self.assertIn("https://checkout.stripe.com/test_session", access.reason)

    def test_stripe_fallback_to_upgrade(self):
        """If Stripe fails, message should suggest /upgrade."""
        user = "tg:test_stripe_fallback"

        for _ in range(20):
            ut.check_access(user)

        # Mock Stripe to fail
        with patch("services.billing.stripe_billing.create_checkout_session",
                    side_effect=Exception("Stripe unavailable")):
            access = ut.check_access(user)
            self.assertFalse(access.allowed)
            self.assertIn("/upgrade", access.reason)

    def test_counter_resets_at_midnight(self):
        """Counter should reset when the date changes."""
        user = "tg:test_reset"

        # Use up 15 messages
        for _ in range(15):
            ut.check_access(user)

        # Simulate midnight by patching _today
        with patch.object(ut, "_today", return_value="2099-01-01"):
            access = ut.check_access(user)
            self.assertTrue(access.allowed)
            self.assertEqual(access.msgs_today, 1)  # reset + first message

    def test_creator_exempt(self):
        """Creator tier has unlimited messages."""
        user = "tg:2030762343"
        ut.set_tier(user, ut.Tier.CREATOR)

        # Send 100 messages — all allowed
        for i in range(100):
            access = ut.check_access(user)
            self.assertTrue(access.allowed, f"Creator message {i+1} should be allowed")


class TestTrial(unittest.TestCase):

    def setUp(self):
        if os.path.exists(_TEST_DB):
            os.unlink(_TEST_DB)

    def test_trial_bypasses_limit(self):
        """Active trial allows unlimited messages."""
        user = "tg:test_trial"
        # Activate 7-day trial
        ut.activate_trial(user, days=7)

        # Send 50 messages — all allowed
        for i in range(50):
            access = ut.check_access(user)
            self.assertTrue(access.allowed, f"Trial message {i+1} should be allowed")
            self.assertEqual(access.reason, "trial_active")

    def test_expired_trial_enforces_limit(self):
        """Expired trial falls back to daily limit."""
        user = "tg:test_expired_trial"
        # Activate trial that already expired
        ut.activate_trial(user, days=0)  # 0 days = expires immediately
        # Manually set trial_end to the past
        conn = ut._conn()
        conn.execute(
            "UPDATE user_tiers SET trial_end = ? WHERE user_id = ?",
            (time.time() - 100, user),
        )
        conn.commit()
        conn.close()

        # Send 20 messages — allowed (normal limit)
        for i in range(20):
            access = ut.check_access(user)
            self.assertTrue(access.allowed, f"Message {i+1} should be allowed")

        # Message 21 — blocked
        access = ut.check_access(user)
        self.assertFalse(access.allowed)

    def test_activate_trial_for_all(self):
        """Global trial activates for all free users."""
        # Create some users
        for uid in ["tg:user_a", "tg:user_b", "tg:user_c"]:
            ut.check_access(uid)  # creates entry

        count = ut.activate_trial_for_all(days=7)
        self.assertGreaterEqual(count, 3)

        # All should have active trial
        for uid in ["tg:user_a", "tg:user_b", "tg:user_c"]:
            status = ut.get_trial_status(uid)
            self.assertTrue(status["active"])
            self.assertGreater(status["days_remaining"], 6)

    def test_trial_resets_blocked_users(self):
        """Activating trial unblocks users who were at limit."""
        user = "tg:test_blocked_then_trial"

        # Exhaust limit
        for _ in range(20):
            ut.check_access(user)

        # Verify blocked
        access = ut.check_access(user)
        self.assertFalse(access.allowed)

        # Activate trial
        ut.activate_trial(user, days=7)

        # Now allowed
        access = ut.check_access(user)
        self.assertTrue(access.allowed)
        self.assertEqual(access.reason, "trial_active")


if __name__ == "__main__":
    unittest.main()
