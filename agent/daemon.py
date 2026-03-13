"""
Vectrax Agent — Daemon Loop
==============================
Background loop that registers, sends heartbeats, and syncs events.
Supports online and offline modes.
"""

import asyncio
import logging
import signal
import sys
from pathlib import Path

# Ensure project root importable
_root = str(Path(__file__).resolve().parents[1])
if _root not in sys.path:
    sys.path.insert(0, _root)

from agent.client import AgentClient
from agent.config import AgentConfig, load_agent_config

logger = logging.getLogger("vectrax.agent.daemon")


class AgentDaemon:
    """Agent background daemon."""

    def __init__(self, config: AgentConfig | None = None):
        self.config = config or load_agent_config()
        self.client = AgentClient(self.config)
        self._running = False

    async def start(self) -> None:
        """Run the daemon loop."""
        self._running = True
        logger.info(
            "Agent starting: id=%s mode=%s core=%s",
            self.config.agent_id,
            self.config.mode,
            self.config.core_url,
        )

        # Register
        result = await self.client.register()
        logger.info("Registration result: %s", result)

        # If online, flush any queued events from offline period
        if self.config.is_online:
            flushed = await self.client.flush_queue()
            if flushed:
                logger.info("Flushed %d queued events", flushed)

        # Heartbeat loop
        while self._running:
            try:
                if self.config.is_online:
                    await self.client.heartbeat()
                await asyncio.sleep(self.config.heartbeat_interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Heartbeat failed: %s", exc)
                await asyncio.sleep(self.config.heartbeat_interval)

        await self.client.close()
        logger.info("Agent stopped")

    def stop(self) -> None:
        self._running = False


async def run_agent(config: AgentConfig | None = None) -> None:
    """Entry point for running the agent daemon."""
    daemon = AgentDaemon(config)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, daemon.stop)
        except NotImplementedError:
            pass  # Windows

    await daemon.start()
