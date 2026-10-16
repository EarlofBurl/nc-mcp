"""ASGI middleware that enforces a static bearer token."""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_401_HEADERS = [
    (b"content-type", b"application/json"),
    (b"www-authenticate", b"Bearer"),
]


class BearerTokenMiddleware:
    """Rejects requests unless `Authorization: Bearer <token>` is present.

    If no token is configured, all requests are allowed (local development
    behind a reverse proxy without auth is then possible — not recommended).
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = (token or "").strip()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.token:
            await self.app(scope, receive, send)
            return

        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        if headers.get("authorization") != f"Bearer {self.token}":
            await self._send_401(send)
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _send_401(send: Send) -> None:
        body = b'{"error": "unauthorized: missing or invalid bearer token"}'
        response: Message = {
            "type": "http.response.start",
            "status": 401,
            "headers": _401_HEADERS,
        }
        await send(response)
        await send({"type": "http.response.body", "body": body})
