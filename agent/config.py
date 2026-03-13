"""
Vectrax Agent — Configuration
================================
Agent settings: core URL, auth token, mode (online/offline).
"""

import os
import json
from dataclasses import dataclass

AGENT_CONFIG_DIR = os.path.expanduser("~/.vectrax")
AGENT_CONFIG_PATH = os.path.join(AGENT_CONFIG_DIR, "agent_config.json")


@dataclass
class AgentConfig:
    core_url: str = os.getenv("VX_CORE_URL", "http://localhost:8900")
    api_token: str = os.getenv("VX_API_TOKEN", "vx-dev-token-local")
    agent_id: str = os.getenv("VX_AGENT_ID", "local-agent-001")
    mode: str = os.getenv("VX_AGENT_MODE", "online")  # online | offline
    heartbeat_interval: int = 60  # seconds
    event_queue_max: int = 1000

    @property
    def is_online(self) -> bool:
        return self.mode == "online"


def load_agent_config() -> AgentConfig:
    """Load agent config from disk, falling back to env/defaults."""
    cfg = AgentConfig()
    if os.path.isfile(AGENT_CONFIG_PATH):
        try:
            with open(AGENT_CONFIG_PATH, "r") as f:
                data = json.load(f)
            if "core_url" in data:
                cfg.core_url = data["core_url"]
            if "api_token" in data:
                cfg.api_token = data["api_token"]
            if "agent_id" in data:
                cfg.agent_id = data["agent_id"]
            if "mode" in data:
                cfg.mode = data["mode"]
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save_agent_config(cfg: AgentConfig) -> None:
    """Persist agent config to disk."""
    os.makedirs(AGENT_CONFIG_DIR, exist_ok=True)
    with open(AGENT_CONFIG_PATH, "w") as f:
        json.dump(
            {
                "core_url": cfg.core_url,
                "api_token": cfg.api_token,
                "agent_id": cfg.agent_id,
                "mode": cfg.mode,
            },
            f,
            indent=2,
        )
