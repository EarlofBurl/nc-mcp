"""Run nc-mcp with uvicorn: `python -m nc_mcp`."""

from __future__ import annotations

import uvicorn

from .config import Config
from .server import create_mcp
from .auth import BearerTokenMiddleware


def main() -> None:
    cfg = Config()
    cfg.validate()
    mcp = create_mcp(cfg)
    app = BearerTokenMiddleware(mcp.http_app(), cfg.mcp_token)
    uvicorn.run(app, host=cfg.mcp_host, port=cfg.mcp_port, log_level="info")


if __name__ == "__main__":
    main()
