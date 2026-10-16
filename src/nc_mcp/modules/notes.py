"""MCP tools for the Nextcloud Notes app (REST API v1)."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from ..nc_client import NC, NcError

API = "/index.php/apps/notes/api/v1/notes"


def _note_meta(note: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": note.get("id"),
        "title": note.get("title"),
        "category": note.get("category") or None,
        "modified": note.get("modified"),
        "favorite": note.get("favorite", False),
        "path": note.get("internalPath"),
    }


def register(mcp: FastMCP, nc: NC) -> None:

    @mcp.tool
    async def notes_list(category: str | None = None) -> list[dict[str, Any]]:
        """List notes (metadata only, without content).

        Args:
            category: Optional category to filter by.
        """
        notes = await nc.rest("GET", API)
        if not isinstance(notes, list):
            raise NcError("Unexpected Notes API response")
        result = [_note_meta(n) for n in notes if category is None or n.get("category") == category]
        return sorted(result, key=lambda n: n["modified"] or 0, reverse=True)

    @mcp.tool
    async def notes_get(note_id: int) -> dict[str, Any]:
        """Fetch a single note including its full content.

        Args:
            note_id: Numeric ID of the note (see notes_list).
        """
        try:
            note = await nc.rest("GET", f"{API}/{note_id}")
        except NcError:
            raise NcError(f"Note {note_id} not found") from None
        return {**_note_meta(note), "content": note.get("content", "")}

    @mcp.tool
    async def notes_create(title: str, content: str = "", category: str = "") -> dict[str, Any]:
        """Create a new note.

        Args:
            title: Note title.
            content: Note content (markdown).
            category: Optional category/folder for the note.
        """
        payload = {"title": title, "content": content}
        if category:
            payload["category"] = category
        note = await nc.rest("POST", API, json_data=payload)
        return _note_meta(note)

    @mcp.tool
    async def notes_update(note_id: int, title: str | None = None, content: str | None = None, category: str | None = None) -> dict[str, Any]:
        """Update an existing note. Only provided fields are changed.

        Args:
            note_id: Numeric ID of the note.
            title: New title (optional).
            content: New content — replaces the whole content (optional).
            category: New category (optional). Empty string removes the category.
        """
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if content is not None:
            payload["content"] = content
        if category is not None:
            payload["category"] = category
        if not payload:
            raise NcError("Nothing to update — provide title, content or category")
        note = await nc.rest("PUT", f"{API}/{note_id}", json_data=payload)
        return _note_meta(note)

    @mcp.tool
    async def notes_delete(note_id: int) -> dict[str, Any]:
        """Delete a note (moved to the Notes trash if available).

        Args:
            note_id: Numeric ID of the note.
        """
        await nc.rest("DELETE", f"{API}/{note_id}")
        return {"deleted": note_id}
