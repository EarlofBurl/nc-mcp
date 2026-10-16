"""MCP tools for the Nextcloud Tasks app (CalDAV VTODO)."""

from __future__ import annotations

import urllib.parse
import uuid
from datetime import UTC, datetime
from typing import Any

from fastmcp import FastMCP
from icalendar import Calendar as ICal, Todo

from ..config import Config
from ..dt import DateTimeError, parse_dt
from ..modules.calendar import _entry, _filename, _report, _set_datetime_prop
from ..nc_client import NC, NcError, require_name


def _todo_entry(filename: str, todo: Any) -> dict[str, Any]:
    """Convert a parsed VTODO component into a plain dict."""
    from ..modules.calendar import _dt_value

    status = str(todo.get("status", "")) or None
    return {
        "filename": filename,
        "uid": str(todo.get("uid", "")),
        "summary": str(todo.get("summary", "")),
        "due": _dt_value(todo.get("due")),
        "priority": int(todo.get("priority")) if todo.get("priority") else None,
        "percent": int(todo.get("percent")) if todo.get("percent") else None,
        "status": status,
        "completed": _dt_value(todo.get("completed")),
        "description": str(todo.get("description", "")) or None,
    }


def register(mcp: FastMCP, nc: NC, cfg: Config) -> None:

    @mcp.tool
    async def tasks_lists() -> list[dict[str, Any]]:
        """List available task lists. Task lists are the same as calendars —
        use calendar_list() to see them; every personal calendar can hold tasks."""
        from ..modules.calendar import _list_calendars

        return await _list_calendars(nc)

    @mcp.tool
    async def tasks_list(task_list: str, include_completed: bool = False) -> list[dict[str, Any]]:
        """List tasks of a task list.

        Args:
            task_list: Calendar/task list name (see calendar_list or tasks_lists).
            include_completed: Also include completed tasks (default false).
        """
        todos = await _report(nc, task_list, "VTODO", None, None, entry_builder=_todo_entry)
        todos = [t for t in todos if include_completed or t["status"] not in ("COMPLETED", "CANCELLED")]
        return sorted(todos, key=lambda t: (t["due"] is None, t["due"] or "", t["summary"]))

    @mcp.tool
    async def task_create(
        task_list: str,
        summary: str,
        due: str | None = None,
        description: str = "",
        priority: int | None = None,
    ) -> dict[str, Any]:
        """Create a task in a task list.

        Args:
            task_list: Calendar/task list name.
            summary: Task title.
            due: Optional due date/datetime, e.g. "2025-12-24" or "2025-12-24T18:00".
            description: Optional notes/description.
            priority: Optional priority 1 (highest) to 9 (lowest).
        """
        task_list = require_name(task_list, "task_list")
        ical = ICal()
        ical.add("prodid", "-//nc-mcp//EN")
        ical.add("version", "2.0")
        todo = Todo()
        uid = str(uuid.uuid4())
        todo.add("uid", uid)
        todo.add("summary", summary)
        todo.add("dtstamp", datetime.now(UTC))
        todo.add("created", datetime.now(UTC))
        if description:
            todo.add("description", description)
        try:
            if due is not None:
                todo.add("due", parse_dt(due, cfg))
        except DateTimeError as exc:
            raise NcError(str(exc)) from exc
        if priority is not None:
            todo.add("priority", min(max(int(priority), 1), 9))
        ical.add_component(todo)

        filename = f"{uid}.ics"
        headers = {"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"}
        resp = await nc.dav(
            "PUT", nc.dav_calendar_path(task_list) + urllib.parse.quote(filename), ical.to_ical(), headers
        )
        if resp.status_code == 404:
            raise NcError(f"Task list '{task_list}' not found. Check calendar_list.", 404)
        nc._check_dav(resp, "Creating task")
        return {"created": summary, "task_list": task_list, "uid": uid, "filename": filename}

    @mcp.tool
    async def task_complete(task_list: str, filename: str) -> dict[str, Any]:
        """Mark a task as completed.

        Args:
            task_list: Calendar/task list name.
            filename: Filename of the task (from tasks_list, e.g. "abc.ics").
        """
        task_list = require_name(task_list, "task_list")
        filename = require_name(filename, "filename")
        path = nc.dav_calendar_path(task_list) + urllib.parse.quote(filename)
        resp = await nc.dav("GET", path)
        if resp.status_code == 404:
            raise NcError(f"Task {filename} not found in {task_list}", 404)
        nc._check_dav(resp, "Reading task")
        ical = ICal.from_ical(resp.text)
        todo = None
        for sub in ical.walk("VTODO"):
            todo = sub
            break
        if todo is None:
            raise NcError("Object contains no VTODO")
        todo["status"] = "COMPLETED"
        _set_datetime_prop(todo, "completed", datetime.now(UTC))
        todo["percent-complete"] = 100
        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        put = await nc.dav("PUT", path, ical.to_ical(), headers)
        nc._check_dav(put, "Completing task")
        return {"completed": filename, "task_list": task_list}

    @mcp.tool
    async def task_delete(task_list: str, filename: str) -> dict[str, Any]:
        """Delete a task.

        Args:
            task_list: Calendar/task list name.
            filename: Filename of the task (from tasks_list).
        """
        task_list = require_name(task_list, "task_list")
        filename = require_name(filename, "filename")
        path = nc.dav_calendar_path(task_list) + urllib.parse.quote(filename)
        resp = await nc.dav("DELETE", path)
        if resp.status_code == 404:
            raise NcError(f"Task {filename} not found in {task_list}", 404)
        nc._check_dav(resp, "Deleting task")
        return {"deleted": filename, "task_list": task_list}
