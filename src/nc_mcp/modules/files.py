"""MCP tools for Nextcloud Files (WebDAV + unified search)."""

from __future__ import annotations

import urllib.parse
from typing import Any

from fastmcp import FastMCP

from ..nc_client import NC, NcError, require_path

MAX_TEXT_BYTES = 200_000


def _display_path(href: str) -> str:
    """Turn a DAV href like /remote.php/dav/files/user/sub/file.txt into /sub/file.txt."""
    prefix = "/remote.php/dav/files/"
    rel = href
    if rel.startswith(prefix):
        rest = rel[len(prefix):]
        rest = rest.split("/", 1)
        rel = "/" + (rest[1] if len(rest) > 1 else "")
    return urllib.parse.unquote(rel)


def _item(entry: dict[str, Any]) -> dict[str, Any]:
    href = entry.get("href", "")
    path = _display_path(href)
    name = path.rstrip("/").rsplit("/", 1)[-1] or "/"
    return {
        "name": name,
        "path": path,
        "is_dir": bool(entry.get("is_dir")),
        "size": int(entry["getcontentlength"]) if entry.get("getcontentlength") else None,
        "content_type": entry.get("getcontenttype"),
        "modified": entry.get("getlastmodified"),
        "etag": entry.get("getetag"),
        "file_id": entry.get("fileid"),
    }


def register(mcp: FastMCP, nc: NC) -> None:

    @mcp.tool
    async def files_list(path: str = "/") -> list[dict[str, Any]]:
        """List the contents of a directory in the Nextcloud files app.

        Args:
            path: Directory path, e.g. "/" or "/Documents". Defaults to the root.

        Returns:
            List of items with name, path, is_dir, size, content_type and modified date.
        """
        entries = await nc.propfind(nc.dav_file_url_path(path), depth=1)
        items = [_item(e) for e in entries[1:]]  # skip the directory itself
        return sorted(items, key=lambda i: (not i["is_dir"], i["name"].lower()))

    @mcp.tool
    async def files_read(path: str, max_bytes: int = 50_000) -> str:
        """Read a text file from Nextcloud and return its content.

        Args:
            path: File path, e.g. "/Documents/todo.md".
            max_bytes: Maximum number of bytes to return (default 50000). The
                result is truncated and marked if the file is longer.

        Binary files (images, office documents, ...) are rejected — use
        files_list to inspect metadata instead.
        """
        resp = await nc.dav("GET", nc.dav_file_url_path(path))
        if resp.status_code == 404:
            raise NcError(f"File not found: {path}", 404)
        nc._check_dav(resp, f"Reading {path}")
        ctype = resp.headers.get("content-type", "")
        if not any(t in ctype for t in ("text/", "json", "xml", "csv", "javascript", "yaml", "x-markdown")):
            raise NcError(
                f"{path} is not a text file (content-type: {ctype or 'unknown'}); "
                "binary content cannot be returned by this tool."
            )
        raw = resp.text[: max_bytes * 2]  # read generous slice, then cut by chars
        if len(resp.content) > max_bytes:
            return raw[:max_bytes] + f"\n\n[... truncated, file has {len(resp.content)} bytes]"
        return raw

    @mcp.tool
    async def files_write(path: str, content: str, overwrite: bool = True) -> dict[str, Any]:
        """Write a text file to Nextcloud (creates or overwrites it).

        Args:
            path: Destination path, e.g. "/Documents/notes.txt". Parent folders
                must already exist — use files_mkdir first.
            content: Full text content of the file.
            overwrite: Whether an existing file may be overwritten (default true).

        Returns:
            Dict with the written path and file size.
        """
        path = require_path(path, "path")
        headers = {"Content-Type": "text/plain; charset=utf-8"}
        if not overwrite:
            headers["If-None-Match"] = "*"
        resp = await nc.dav("PUT", nc.dav_file_url_path(path), content, headers)
        if resp.status_code == 412:
            raise NcError(f"{path} already exists (use overwrite=true to replace it)", 412)
        nc._check_dav(resp, f"Writing {path}")
        return {"path": path, "bytes_written": len(content.encode())}

    @mcp.tool
    async def files_mkdir(path: str) -> dict[str, Any]:
        """Create a folder in Nextcloud. Parent folders must exist.

        Args:
            path: Folder path, e.g. "/Documents/NewFolder".
        """
        path = require_path(path, "path")
        resp = await nc.dav("MKCOL", nc.dav_file_url_path(path))
        if resp.status_code == 405:
            raise NcError(f"Folder {path} already exists", 405)
        nc._check_dav(resp, f"Creating folder {path}")
        return {"created": path}

    @mcp.tool
    async def files_move(source: str, destination: str, overwrite: bool = False) -> dict[str, Any]:
        """Move or rename a file/folder within Nextcloud.

        Args:
            source: Existing path, e.g. "/tmp/file.txt".
            destination: New path, e.g. "/Archive/file.txt".
            overwrite: Whether to replace an existing destination.
        """
        source = require_path(source, "source")
        destination = require_path(destination, "destination")
        headers = {
            "Destination": f"{nc.base_url}{nc.dav_file_url_path(destination)}",
            "Overwrite": "T" if overwrite else "F",
        }
        resp = await nc.dav("MOVE", nc.dav_file_url_path(source), None, headers)
        if resp.status_code == 412:
            raise NcError(f"Destination {destination} already exists (use overwrite=true)", 412)
        if resp.status_code == 404:
            raise NcError(f"Source not found: {source}", 404)
        nc._check_dav(resp, f"Moving {source} to {destination}")
        return {"moved": source, "to": destination}

    @mcp.tool
    async def files_copy(source: str, destination: str, overwrite: bool = False) -> dict[str, Any]:
        """Copy a file/folder within Nextcloud.

        Args:
            source: Existing path.
            destination: New path.
            overwrite: Whether to replace an existing destination.
        """
        source = require_path(source, "source")
        destination = require_path(destination, "destination")
        headers = {
            "Destination": f"{nc.base_url}{nc.dav_file_url_path(destination)}",
            "Overwrite": "T" if overwrite else "F",
        }
        resp = await nc.dav("COPY", nc.dav_file_url_path(source), None, headers)
        if resp.status_code == 412:
            raise NcError(f"Destination {destination} already exists (use overwrite=true)", 412)
        if resp.status_code == 404:
            raise NcError(f"Source not found: {source}", 404)
        nc._check_dav(resp, f"Copying {source} to {destination}")
        return {"copied": source, "to": destination}

    @mcp.tool
    async def files_delete(path: str) -> dict[str, Any]:
        """Delete a file or folder (moves it to the Nextcloud trashbin, recoverable there).

        Args:
            path: Path of the file/folder to delete.
        """
        path = require_path(path, "path")
        resp = await nc.dav("DELETE", nc.dav_file_url_path(path))
        if resp.status_code == 404:
            raise NcError(f"Not found: {path}", 404)
        nc._check_dav(resp, f"Deleting {path}")
        return {"deleted": path, "note": "recoverable from the Nextcloud trashbin"}

    @mcp.tool
    async def files_search(query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search files by name across the whole Nextcloud (full-text of names).

        Args:
            query: Search term, matched against file and folder names.
            limit: Maximum number of results (default 20).
        """
        data = await nc.ocs(
            "GET",
            "/ocs/v2.php/search/providers/files/search",
            params={"term": query, "from": 0, "limit": limit},
        )
        results = []
        for entry in (data or {}).get("entries", []):
            attributes = entry.get("attributes", {}) or {}
            results.append(
                {
                    "title": entry.get("title"),
                    "subline": entry.get("subline"),
                    "path": attributes.get("path") or entry.get("subline"),
                    "info": attributes.get("extraInfo"),
                }
            )
        return results
