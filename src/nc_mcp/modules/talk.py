"""MCP tools for Nextcloud Talk (OCS API).

Note: some Apache setups break the plural `/rooms` endpoint (rewrite rules);
the client automatically falls back to the singular `/room` variant.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from ..nc_client import NC, NcError, require_name

CHAT_PATH = "/ocs/v2.php/apps/spreed/api/v1/chat/{token}"
ROOM_TYPES = {
    "1": "one-to-one",
    "2": "group",
    "3": "public",
    "4": "note-to-self",
    "5": "video call",
    "6": "former one-to-one",
}


def register(mcp: FastMCP, nc: NC, rooms_path: str) -> None:
    # Remember which endpoint variant works on this server.
    state: dict[str, str] = {}

    async def _rooms_endpoint() -> str:
        if "path" in state:
            return state["path"]
        try:
            await nc.ocs("GET", rooms_path)
            state["path"] = rooms_path
        except NcError:
            fallback = rooms_path.rstrip("s") if rooms_path.endswith("rooms") else rooms_path + "s"
            state["path"] = fallback
        return state["path"]

    @mcp.tool
    async def talk_rooms_list() -> list[dict[str, Any]]:
        """List Talk rooms/conversations with their tokens (needed for reading
        and sending messages)."""
        endpoint = await _rooms_endpoint()
        rooms = await nc.ocs("GET", endpoint)
        if not isinstance(rooms, list):
            raise NcError("Unexpected Talk API response")
        result = []
        for room in rooms:
            last = room.get("lastMessage") or {}
            result.append(
                {
                    "id": room.get("id"),
                    "token": room.get("token"),
                    "name": room.get("name"),
                    "display_name": room.get("displayName"),
                    "type": ROOM_TYPES.get(str(room.get("type")), room.get("type")),
                    "unread_messages": room.get("unreadMessages"),
                    "last_message": {
                        "from": last.get("actorDisplayName"),
                        "text": (last.get("message", "") or "")[:200],
                    } if last else None,
                }
            )
        return result

    @mcp.tool
    async def talk_send_message(room_token: str, message: str) -> dict[str, Any]:
        """Send a message to a Talk room (as the Nextcloud user).

        Args:
            room_token: Token of the room (see talk_rooms_list).
            message: Message text. Markdown formatting is supported.
        """
        room_token = require_name(room_token, "room_token")
        await nc.ocs("POST", CHAT_PATH.format(token=room_token), data={"message": message})
        return {"sent": True, "room_token": room_token, "length": len(message)}

    @mcp.tool
    async def talk_read_messages(
        room_token: str,
        limit: int = 20,
        last_known_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read the most recent messages of a Talk room.

        Args:
            room_token: Token of the room (see talk_rooms_list).
            limit: Number of messages (default 20).
            last_known_id: Optional message id; older messages before this one are returned.
        """
        room_token = require_name(room_token, "room_token")
        params: dict[str, Any] = {"lookIntoFuture": 0, "limit": limit, "includeLastKnown": 0}
        if last_known_id is not None:
            params["lastKnownMessageId"] = last_known_id
        messages = await nc.ocs("GET", CHAT_PATH.format(token=room_token), params=params)
        if not isinstance(messages, list):
            raise NcError("Unexpected Talk API response")
        result = []
        for msg in messages:
            result.append(
                {
                    "id": msg.get("id"),
                    "from": msg.get("actorDisplayName"),
                    "actor_type": msg.get("actorType"),
                    "timestamp": msg.get("timestamp"),
                    "message": msg.get("message"),
                    "reactions": msg.get("reactions") or None,
                }
            )
        return result
