"""
Action types — the ONLY code that performs side-effects in the recovery engine.

Each Action subclass implements:
  - execute()   → performs the real side-effect; returns True on success.
  - dry_run()   → logs what WOULD happen; must NOT produce side-effects.
  - describe()  → short human-readable summary for logs and ledger.

Strategies declare Actions in action_plan / rollback_plan but do NOT
run them.  Only RecoveryExecutor.execute() invokes these.

Design principle: each Action is atomic and self-contained.  If execute()
raises, the executor treats it as a failure and triggers rollback.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger("vectrax.recovery.actions")


class Action(ABC):
    """Abstract base for all operational actions."""

    @abstractmethod
    def execute(self) -> bool:
        """Perform the side-effect.  Returns True on success."""

    @abstractmethod
    def dry_run(self) -> None:
        """Log what would happen.  Must NOT produce side-effects."""

    @abstractmethod
    def describe(self) -> str:
        """Short human-readable summary."""


@dataclass(frozen=True)
class RestartContainer(Action):
    """docker compose restart / up -d on a named container.

    Uses subprocess — NOT the docker SDK — so it works on any host with
    the docker CLI available, without extra pip deps.
    """

    container_name: str
    compose_dir: str = "/opt/vectrax"
    mode: str = "up"  # "restart" (soft) or "up" (recreate; applies .env changes)

    def execute(self) -> bool:
        if self.mode == "up":
            cmd = ["docker", "compose", "up", "-d", self.container_name]
        else:
            cmd = ["docker", "compose", "restart", self.container_name]
        logger.info("EXECUTE: %s (cwd=%s)", " ".join(cmd), self.compose_dir)
        try:
            result = subprocess.run(
                cmd,
                cwd=self.compose_dir,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                logger.error(
                    "RestartContainer failed: rc=%d stderr=%s",
                    result.returncode, result.stderr[:500],
                )
                return False
            return True
        except subprocess.TimeoutExpired:
            logger.error("RestartContainer timed out after 120s")
            return False
        except Exception as exc:
            logger.exception("RestartContainer crashed: %s", exc)
            return False

    def dry_run(self) -> None:
        logger.info("DRY-RUN: would %s container=%s in %s", self.mode, self.container_name, self.compose_dir)

    def describe(self) -> str:
        return f"RestartContainer({self.container_name}, mode={self.mode})"


# NOTE: ExecShell was intentionally NOT included in v1.
# Rationale: a typed-actions catalogue is the whole point of the engine.
# An escape-hatch like ExecShell bypasses audit, reuse, and testability.
# If a new recovery needs something new (e.g. ReloadCaddy, RenewCert,
# FlushPool, RotateLog), add a typed Action class — do NOT reintroduce
# shell execution.


@dataclass(frozen=True)
class SetEnv(Action):
    """Set or update a key=value in a .env file.  Idempotent.

    Reads the file, replaces the line if the key exists, otherwise appends.
    Preserves all other lines untouched.

    WARNING: this modifies persistent configuration.  Any strategy using
    this MUST have a sensible rollback_plan that reverts the change.
    """

    env_file: str
    key: str
    value: str

    def execute(self) -> bool:
        logger.info("EXECUTE SetEnv: %s = %s in %s", self.key, self._redact_value(), self.env_file)
        try:
            if not os.path.exists(self.env_file):
                logger.error("SetEnv: file not found: %s", self.env_file)
                return False
            with open(self.env_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

            pattern = re.compile(r"^\s*" + re.escape(self.key) + r"\s*=")
            new_lines = []
            replaced = False
            for ln in lines:
                if pattern.match(ln):
                    new_lines.append(f"{self.key}={self.value}\n")
                    replaced = True
                else:
                    new_lines.append(ln)
            if not replaced:
                if new_lines and not new_lines[-1].endswith("\n"):
                    new_lines.append("\n")
                new_lines.append(f"{self.key}={self.value}\n")

            with open(self.env_file, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            return True
        except Exception as exc:
            logger.exception("SetEnv crashed: %s", exc)
            return False

    def _redact_value(self) -> str:
        """Don't log full secrets; show first char + length."""
        if any(s in self.key.lower() for s in ("secret", "token", "key", "password")):
            return f"<redacted, len={len(self.value)}>"
        return self.value

    def dry_run(self) -> None:
        logger.info(
            "DRY-RUN: would set %s=%s in %s",
            self.key, self._redact_value(), self.env_file,
        )

    def describe(self) -> str:
        return f"SetEnv({self.key}, file={self.env_file})"


@dataclass(frozen=True)
class CallHttp(Action):
    """Make an HTTP call.  Used e.g. to call Telegram's setWebhook/deleteWebhook.

    Kept intentionally simple: uses httpx with a short timeout.  Does NOT
    retry — the strategy can schedule retries via its action_plan.
    """

    url: str
    method: str = "GET"
    json_body: Optional[Dict[str, Any]] = None
    headers: Optional[Dict[str, str]] = None
    timeout_s: int = 15
    expect_status: int = 200

    def execute(self) -> bool:
        logger.info("EXECUTE http: %s %s", self.method, self._redact_url())
        try:
            import httpx
            with httpx.Client(timeout=self.timeout_s) as client:
                r = client.request(
                    self.method.upper(),
                    self.url,
                    json=self.json_body,
                    headers=self.headers,
                )
                if r.status_code != self.expect_status:
                    logger.error(
                        "CallHttp unexpected status: got %d, expected %d. body=%s",
                        r.status_code, self.expect_status, r.text[:200],
                    )
                    return False
                return True
        except Exception as exc:
            logger.exception("CallHttp crashed: %s", exc)
            return False

    def _redact_url(self) -> str:
        """Redact bot tokens and secrets embedded in URLs."""
        # Telegram bot API URLs look like https://api.telegram.org/bot<TOKEN>/method
        u = re.sub(r"/bot[0-9]+:[A-Za-z0-9_-]+/", "/bot***REDACTED***/", self.url)
        # Webhook secret paths
        u = re.sub(r"/webhook/telegram/[^/?]+", "/webhook/telegram/***", u)
        return u

    def dry_run(self) -> None:
        logger.info("DRY-RUN: would call %s %s", self.method, self._redact_url())

    def describe(self) -> str:
        return f"CallHttp({self.method} {self._redact_url()[:60]})"


@dataclass(frozen=True)
class NotifyHuman(Action):
    """Escalation action — sends a notification to a human channel.

    v1: writes to a file that Mario monitors manually + logs at WARNING.
    v2: Telegram message + email SMTP (once those are reliable).
    Intentionally KEEP SIMPLE in v1; the whole point is the motor escalates
    to human when it can't auto-recover.
    """

    message: str
    channel: str = "file"  # "file" | "telegram" | "email" (v1: only file works)
    alert_file: str = "/tmp/vectrax_recovery_alerts.log"

    def execute(self) -> bool:
        # Always log at WARNING level regardless of channel
        logger.warning("RECOVERY ESCALATION [%s]: %s", self.channel, self.message)
        try:
            with open(self.alert_file, "a", encoding="utf-8") as f:
                import time as _t
                f.write(f"[{_t.strftime('%Y-%m-%d %H:%M:%S UTC', _t.gmtime())}] {self.message}\n")
            return True
        except Exception as exc:
            logger.exception("NotifyHuman crashed: %s", exc)
            return False

    def dry_run(self) -> None:
        logger.warning("DRY-RUN ESCALATION [%s]: %s", self.channel, self.message)

    def describe(self) -> str:
        preview = self.message[:60] + ("..." if len(self.message) > 60 else "")
        return f"NotifyHuman({self.channel}: {preview})"
