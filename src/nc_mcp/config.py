"""Environment-based configuration for nc-mcp."""

from __future__ import annotations

import os


class ConfigError(RuntimeError):
    pass


class Config:
    def __init__(self) -> None:
        self.nc_url = os.environ.get("NC_URL", "https://morschel.org").rstrip("/")
        self.nc_username = os.environ.get("NC_USERNAME", "")
        self.nc_app_password = os.environ.get("NC_APP_PASSWORD", "")
        # Bearer token clients must present to reach the MCP server.
        self.mcp_token = os.environ.get("MCP_TOKEN", "")
        self.mcp_host = os.environ.get("MCP_HOST", "127.0.0.1")
        self.mcp_port = int(os.environ.get("MCP_PORT", "8010"))
        # httpx timeout for Nextcloud calls (seconds).
        self.nc_timeout = float(os.environ.get("NC_TIMEOUT", "30"))
        # Default timezone applied to naive datetimes (e.g. "2025-12-24T18:00:00").
        self.default_tz = os.environ.get("NC_DEFAULT_TZ", "Europe/Berlin")
        # Some Apache setups with rewrite rules break the plural endpoint; the
        # client falls back to the singular variant automatically.
        self.talk_rooms_path = os.environ.get(
            "NC_TALK_ROOMS_PATH", "/ocs/v2.php/apps/spreed/api/v4/rooms"
        )

    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("NC_USERNAME", self.nc_username),
                ("NC_APP_PASSWORD", self.nc_app_password),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                f"Missing required environment variables: {', '.join(missing)}. "
                "Set them in .env or the container environment."
            )
        if not self.nc_url.startswith(("http://", "https://")):
            raise ConfigError(f"NC_URL must start with http:// or https:// (got {self.nc_url!r})")
