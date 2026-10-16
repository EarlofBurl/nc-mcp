"""ASGI entry point for uvicorn: `uvicorn nc_mcp.app:app`.

Wraps the FastMCP streamable-HTTP app with the bearer-token middleware.
"""

from __future__ import annotations

from .auth import BearerTokenMiddleware
from .config import Config
from .server import create_mcp


def _build():
    cfg = Config()
    cfg.validate()
    mcp = create_mcp(cfg)
    return BearerTokenMiddleware(mcp.http_app(), cfg.mcp_token)


app = _build()
