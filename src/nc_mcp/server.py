"""FastMCP server factory for nc-mcp."""

from __future__ import annotations

from fastmcp import FastMCP

from .config import Config
from .nc_client import NC
from .modules import contacts, files, notes, tasks
from .modules import calendar as calendar_mod
from .modules import talk as talk_mod

INSTRUCTIONS = """\
This server gives access to a Nextcloud instance (files, notes, calendar,
tasks, contacts and Talk chat).

Guidelines:
- Discovery: use files_list, calendar_list, notes_list, contacts_addressbooks_list
  or talk_rooms_list first to find ids/names before using write tools.
- Files: read freely; for destructive operations (files_delete, files_move with
  overwrite) state clearly what will be removed before doing it.
- Notes are a good place to persist results, summaries or plans.
- Talk: use talk_send_message to inform the user; rooms are identified by token.
- Datetimes are ISO format; without an offset the server default timezone applies.
"""


def create_mcp(cfg: Config) -> FastMCP:
    mcp = FastMCP(name="nc-mcp", instructions=INSTRUCTIONS)
    nc = NC(cfg.nc_url, cfg.nc_username, cfg.nc_app_password, timeout=cfg.nc_timeout)

    files.register(mcp, nc)
    notes.register(mcp, nc)
    calendar_mod.register(mcp, nc, cfg)
    tasks.register(mcp, nc, cfg)
    contacts.register(mcp, nc)
    talk_mod.register(mcp, nc, cfg.talk_rooms_path)
    return mcp
