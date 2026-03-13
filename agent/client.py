"""
Vectrax Agent — HTTP Client
==============================
Communicates with the Core Central Service.
Handles register, send events, fetch proposals.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from agent.config import AgentConfig, load_agent_config

logger = logging.getLogger("vectrax.agent.client")

# Offline event queue file
_QUEUE_DIR = os.path.expanduser("~/.vectrax")
_QUEUE_FILE = os.path.join(_QUEUE_DIR, "event_queue.jsonl")


class AgentClient:
    """HTTP client for Agent ↔ Core communication."""

    def __init__(self, config: Optional[AgentConfig] = None):
        self.config = config or load_agent_config()
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.config.core_url,
                headers={"Authorization": f"Bearer {self.config.api_token}"},
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Core connectivity
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Check if Core is reachable."""
        try:
            client = await self._get_client()
            resp = await client.get("/v1/health")
            return resp.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    async def send_event(
        self,
        event_type: str,
        data: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Send an event to Core. Queues locally if offline."""
        payload = {
            "event_type": event_type,
            "agent_id": self.config.agent_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "data": data or {},
        }

        if not self.config.is_online:
            self._queue_event(payload)
            return {"accepted": True, "queued": True, "message": "Queued offline"}

        try:
            client = await self._get_client()
            resp = await client.post("/v1/events", json=payload)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("Failed to send event, queuing offline: %s", exc)
            self._queue_event(payload)
            return {"accepted": True, "queued": True, "message": str(exc)}

    async def register(self) -> Dict[str, Any]:
        """Register this agent with Core."""
        import platform

        return await self.send_event(
            "agent.register",
            data={
                "hostname": platform.node(),
                "platform": platform.system(),
                "python": platform.python_version(),
                "agent_id": self.config.agent_id,
                "mode": self.config.mode,
            },
        )

    async def heartbeat(self) -> Dict[str, Any]:
        """Send heartbeat to Core."""
        return await self.send_event("agent.heartbeat", data={"status": "alive"})

    # ------------------------------------------------------------------
    # Proposals
    # ------------------------------------------------------------------

    async def fetch_proposal(
        self,
        description: str,
        model: str = "qwen2.5-coder:7b",
    ) -> Dict[str, Any]:
        """Request a proposal from Core."""
        client = await self._get_client()
        resp = await client.post(
            "/v1/actions/propose",
            json={
                "description": description,
                "model": model,
                "agent_id": self.config.agent_id,
            },
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Offline queue
    # ------------------------------------------------------------------

    def _queue_event(self, payload: Dict[str, Any]) -> None:
        """Append event to local queue file."""
        os.makedirs(_QUEUE_DIR, exist_ok=True)
        with open(_QUEUE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    async def flush_queue(self) -> int:
        """Send all queued events to Core. Returns count sent."""
        if not os.path.isfile(_QUEUE_FILE):
            return 0

        with open(_QUEUE_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()

        if not lines:
            return 0

        sent = 0
        remaining = []

        client = await self._get_client()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                resp = await client.post("/v1/events", json=payload)
                if resp.status_code < 400:
                    sent += 1
                else:
                    remaining.append(line + "\n")
            except Exception:
                remaining.append(line + "\n")

        # Rewrite queue with unsent events
        with open(_QUEUE_FILE, "w", encoding="utf-8") as f:
            f.writelines(remaining)

        logger.info("Flushed %d queued events, %d remaining", sent, len(remaining))
        return sent
