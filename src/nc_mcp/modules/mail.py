"""MCP tools for the Nextcloud Mail app (internal REST API).

Requires the Mail app with at least one configured mail account. The Mail API
is the app's frontend API — it needs the OCS-APIRequest header (bypasses the
CSRF check) and returns plain JSON, partly wrapped in {"status", "data"}.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from fastmcp import FastMCP

from ..nc_client import NC, NcError

API = "/index.php/apps/mail/api"
OC = {"OCS-APIRequest": "true"}

TAG_RE = re.compile(r"<[^>]+>")


def _unwrap(payload: Any, what: str) -> Any:
    """Handle {"status": "fail", "data": {...}} error envelopes."""
    if isinstance(payload, dict) and payload.get("status") == "fail":
        data = payload.get("data") or {}
        raise NcError(f"{what} failed: {data.get('message', 'unknown error')}")
    if isinstance(payload, dict) and "status" in payload and "data" in payload:
        return payload["data"]
    return payload


def _recipients(value: list[str] | str) -> list[dict[str, str]]:
    """Normalize 'to'/'cc'/'bcc' inputs into Mail API recipient dicts."""
    if isinstance(value, str):
        value = [value]
    recipients = []
    for email in value:
        email = (email or "").strip()
        if not email or "@" not in email:
            raise NcError(f"Invalid email address: {email!r}")
        recipients.append({"email": email, "label": email})
    return recipients


def _iso(ts: Any) -> str | None:
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(int(ts), tz=UTC).isoformat()
    return None


def _message_meta(m: dict[str, Any]) -> dict[str, Any]:
    flags = m.get("flags") or {}
    return {
        "id": m.get("databaseId"),
        "subject": m.get("subject") or "(no subject)",
        "from": [a.get("email") for a in m.get("from", [])],
        "to": [a.get("email") for a in m.get("to", [])],
        "date": _iso(m.get("dateInt")),
        "seen": bool(flags.get("seen")),
        "flagged": bool(flags.get("flagged")),
        "has_attachments": bool(flags.get("hasAttachments")),
        "preview": (m.get("previewText") or "")[:200] or None,
    }


def _strip_html(text: str) -> str:
    if not text or not text.lstrip().startswith("<"):
        return text
    text = re.sub(r"<(br|/p|/div|/tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    return TAG_RE.sub("", text).strip()


def register(mcp: FastMCP, nc: NC) -> None:

    @mcp.tool
    async def mail_accounts_list() -> list[dict[str, Any]]:
        """List the mail accounts configured in the Nextcloud Mail app."""
        accounts = await nc.rest("GET", f"{API}/accounts", extra_headers=OC)
        return [
            {
                "id": a.get("id"),
                "name": a.get("name"),
                "email": a.get("emailAddress"),
                "imap_host": a.get("imapHost"),
            }
            for a in accounts
        ]

    @mcp.tool
    async def mail_mailboxes_list(account_id: int) -> list[dict[str, Any]]:
        """List the mailboxes (folders) of a mail account.

        Args:
            account_id: Account id (see mail_accounts_list).
        """
        payload = await nc.rest(
            "GET", f"{API}/mailboxes", params={"accountId": account_id}, extra_headers=OC
        )
        boxes = payload.get("mailboxes", []) if isinstance(payload, dict) else payload
        return [
            {
                "id": b.get("databaseId"),
                "name": b.get("name"),
                "special_role": b.get("specialRole") or None,
                "unread": b.get("unread"),
            }
            for b in boxes
        ]

    @mcp.tool
    async def mail_messages_list(
        mailbox_id: int,
        filter: str = "all",
        sync: bool = True,
    ) -> list[dict[str, Any]]:
        """List the messages of a mailbox, newest first.

        Args:
            mailbox_id: Mailbox id (see mail_mailboxes_list).
            filter: 'all', 'unread' or 'flagged' (default 'all').
            sync: Trigger a mailbox sync first so recent mails show up (default true).
        """
        if sync:
            for init in (False, True):
                try:
                    payload = await nc.rest(
                        "POST",
                        f"{API}/mailboxes/{mailbox_id}/sync",
                        json_data={"syncToken": None, "ids": [], "init": init},
                        extra_headers=OC,
                    )
                    _unwrap(payload, "Mailbox sync")
                    break
                except NcError:
                    continue

        payload = await nc.rest(
            "GET", f"{API}/messages", params={"mailboxId": mailbox_id, "filter": filter}, extra_headers=OC
        )
        if isinstance(payload, dict) and payload.get("status") == "fail":
            data = payload.get("data") or {}
            raise NcError(
                f"Could not list mailbox {mailbox_id}: {data.get('message', 'unknown error')}"
            )
        return [_message_meta(m) for m in payload]

    @mcp.tool
    async def mail_message_get(message_id: int) -> dict[str, Any]:
        """Fetch a single message with its full body text.

        Args:
            message_id: Message id (see mail_messages_list).
        """
        meta = await nc.rest("GET", f"{API}/messages/{message_id}", extra_headers=OC)
        body = await nc.rest("GET", f"{API}/messages/{message_id}/body", extra_headers=OC)
        body_text = _strip_html(str(body.get("body") or ""))
        result = _message_meta(meta)
        result.update(
            {
                "cc": [a.get("email") for a in body.get("cc", [])],
                "bcc": [a.get("email") for a in body.get("bcc", [])],
                "body": body_text or None,
                "body_html": bool((body.get("body") or "").lstrip().startswith("<")),
                "attachments": [
                    a.get("name") for a in (body.get("attachments") or [])
                ],
            }
        )
        return result

    @mcp.tool
    async def mail_send(
        account_id: int,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> dict[str, Any]:
        """Send an email via the Nextcloud Mail app (plain text).

        Args:
            account_id: Account id (see mail_accounts_list).
            to: Recipient email addresses.
            subject: Subject line.
            body: Plain text body.
            cc: Optional carbon-copy recipients.
            bcc: Optional blind carbon-copy recipients.
        """
        if not to:
            raise NcError("'to' must contain at least one recipient")
        payload = {
            "accountId": account_id,
            "to": _recipients(to),
            "cc": _recipients(cc) if cc else [],
            "bcc": _recipients(bcc) if bcc else [],
            "subject": subject,
            "bodyPlain": body,
            "bodyHtml": None,
            "editorBody": None,
            "isHtml": False,
            "smimeSign": False,
            "smimeEncrypt": False,
            "attachments": [],
        }
        created = _unwrap(
            await nc.rest("POST", f"{API}/outbox", json_data=payload, extra_headers=OC),
            "Creating message in outbox",
        )
        outbox_id = (created or {}).get("id")
        if outbox_id is None:
            raise NcError("Mail app did not return an outbox id")
        send = _unwrap(
            await nc.rest("POST", f"{API}/outbox/{outbox_id}", extra_headers=OC),
            "Sending message",
        )
        return {"sent": True, "to": to, "subject": subject, "outbox_id": outbox_id, "response": send}
