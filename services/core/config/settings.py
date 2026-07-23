"""
Vectrax Core Central Service — Settings
=========================================
Environment-based configuration using pydantic-style defaults.
Supports dev and local environments.
"""

import os
from dataclasses import dataclass, field
from typing import List

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_ENV = os.getenv("VX_ENV", "dev")


@dataclass
class Settings:
    """Core service configuration."""

    env: str = _ENV
    host: str = os.getenv("VX_CORE_HOST", "0.0.0.0")
    port: int = int(os.getenv("VX_CORE_PORT", "8900"))

    # Auth (no insecure default: legacy owner token is opt-in via VX_API_TOKEN)
    api_token: str = os.getenv("VX_API_TOKEN", "")
    jwt_secret: str = os.getenv("VX_JWT_SECRET", "vectrax-dev-secret-change-me")

    # CORS
    cors_origins: List[str] = field(default_factory=lambda: [
        "http://localhost:*",
        "http://127.0.0.1:*",
    ])

    # Rate limiting
    rate_limit_rpm: int = int(os.getenv("VX_RATE_LIMIT_RPM", "120"))

    # Logging
    log_level: str = os.getenv("VX_LOG_LEVEL", "INFO")

    # Paths
    project_root: str = os.getenv(
        "VX_PROJECT_ROOT",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
    )

    # Database
    db_path: str = os.getenv(
        "VX_DB_PATH",
        os.path.join(os.path.expanduser("~"), ".vectrax", "vectrax.db"),
    )

    # Vector database
    vector_db_url: str = os.getenv("VX_VECTOR_DB_URL", "")

    @property
    def is_dev(self) -> bool:
        return self.env in ("dev", "local")

    @property
    def is_production(self) -> bool:
        return self.env == "production"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_settings = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
