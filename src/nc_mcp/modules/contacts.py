"""MCP tools for the Nextcloud Contacts app (CardDAV)."""

from __future__ import annotations

import re
import urllib.parse
import uuid
from datetime import UTC, datetime
from typing import Any

from fastmcp import FastMCP

from ..nc_client import NC, NcError, require_name

QUERY_ALL = """<?xml version="1.0"?>
<c:addressbook-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">
  <d:prop><d:getetag/><c:address-data/></d:prop>
</c:addressbook-query>
"""

QUERY_FILTER = """<?xml version="1.0"?>
<c:addressbook-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">
  <d:prop><d:getetag/><c:address-data/></d:prop>
  <c:filter>
    <c:prop-filter name="{prop}">
      <c:text-match collation="i;unicode-casemap">{query}</c:text-match>
    </c:prop-filter>
  </c:filter>
</c:addressbook-query>
"""


def _filename(href: str) -> str:
    return urllib.parse.unquote(href.rstrip("/").rsplit("/", 1)[-1])


def _vcard_field(vcard: str, name: str) -> list[str]:
    """Extract values of a simple vCard property (EMAIL, TEL, ORG, ...)."""
    values = []
    for line in vcard.replace("\r\n", "\n").split("\n"):
        match = re.match(rf"^{name}[^:]*:(.*)$", line.strip(), re.IGNORECASE)
        if match:
            values.append(match.group(1).strip())
    return values


def _contact(filename: str, vcard: str) -> dict[str, Any]:
    fn = _vcard_field(vcard, "FN")
    uid = _vcard_field(vcard, "UID")
    return {
        "filename": filename,
        "uid": uid[0] if uid else None,
        "fn": fn[0] if fn else filename,
        "emails": _vcard_field(vcard, "EMAIL"),
        "phones": _vcard_field(vcard, "TEL"),
        "org": (_vcard_field(vcard, "ORG")[0] if _vcard_field(vcard, "ORG") else None),
        "categories": [c.strip() for c in (_vcard_field(vcard, "CATEGORIES")[0].split(",") if _vcard_field(vcard, "CATEGORIES") else [])],
    }


def _build_vcard(fn: str, emails: list[str], phones: list[str], org: str) -> str:
    lines = ["BEGIN:VCARD", "VERSION:3.0", f"UID:{uuid.uuid4()}", f"FN:{fn}", "N:;;;;"]
    for email in emails:
        lines.append(f"EMAIL;TYPE=INTERNET:{email}")
    for phone in phones:
        lines.append(f"TEL;TYPE=VOICE:{phone}")
    if org:
        lines.append(f"ORG:{org}")
    lines.append(f"REV:{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


def register(mcp: FastMCP, nc: NC) -> None:

    @mcp.tool
    async def contacts_addressbooks_list() -> list[dict[str, Any]]:
        """List the user's address books (e.g. 'kontakte', 'Konten')."""
        entries = await nc.propfind(nc.dav_addressbook_path(""), depth=1)
        books = []
        for e in entries:
            if not e.get("is_dir"):
                continue
            name = _filename(e["href"])
            if name:
                books.append({"name": name, "display_name": e.get("displayname") or name})
        return books

    @mcp.tool
    async def contacts_list(addressbook: str, query: str = "") -> list[dict[str, Any]]:
        """List (and optionally filter) contacts of an address book.

        Args:
            addressbook: Address book name (see contacts_addressbooks_list), e.g. "kontakte".
            query: Optional case-insensitive filter on full name and email/phone/org.
        """
        body = QUERY_FILTER.format(prop="FN", query=query) if query else QUERY_ALL
        path = nc.dav_addressbook_path(addressbook)
        resp = await nc.dav("REPORT", path, body, {"Depth": "1"})
        if resp.status_code == 404:
            raise NcError(f"Address book '{addressbook}' not found. Check contacts_addressbooks_list.", 404)
        nc._check_dav(resp, "Listing contacts")

        from ..nc_client import parse_multi_status

        contacts = []
        for e in parse_multi_status(resp.text):
            data = e.get("address-data")
            if data:
                contacts.append(_contact(_filename(e["href"]), data))
        if query:
            q = query.lower()
            contacts = [
                c for c in contacts
                if q in c["fn"].lower() or any(q in v.lower() for v in c["emails"] + c["phones"] + ([c["org"]] if c["org"] else []))
            ]
        return sorted(contacts, key=lambda c: c["fn"].lower())

    @mcp.tool
    async def contact_create(
        addressbook: str,
        fn: str,
        emails: list[str] | None = None,
        phones: list[str] | None = None,
        org: str = "",
    ) -> dict[str, Any]:
        """Create a new contact.

        Args:
            addressbook: Address book name (see contacts_addressbooks_list).
            fn: Full name of the contact.
            emails: List of email addresses.
            phones: List of phone numbers.
            org: Organization/company.
        """
        addressbook = require_name(addressbook, "addressbook")
        vcard = _build_vcard(fn, emails or [], phones or [], org)
        uid = _vcard_field(vcard, "UID")[0]
        filename = f"{uid}.vcf"
        headers = {"Content-Type": "text/vcard; charset=utf-8", "If-None-Match": "*"}
        resp = await nc.dav("PUT", nc.dav_addressbook_path(addressbook) + urllib.parse.quote(filename), vcard, headers)
        if resp.status_code == 404:
            raise NcError(f"Address book '{addressbook}' not found.", 404)
        if resp.status_code in (403, 409, 415):
            raise NcError(f"Address book rejected the contact (HTTP {resp.status_code}): {resp.text[:200]}", resp.status_code)
        nc._check_dav(resp, "Creating contact")
        return {"created": fn, "addressbook": addressbook, "filename": filename}

    @mcp.tool
    async def contact_delete(addressbook: str, filename: str) -> dict[str, Any]:
        """Delete a contact from an address book.

        Args:
            addressbook: Address book name.
            filename: Filename of the contact (from contacts_list, e.g. "abc.vcf").
        """
        addressbook = require_name(addressbook, "addressbook")
        filename = require_name(filename, "filename")
        path = nc.dav_addressbook_path(addressbook) + urllib.parse.quote(filename)
        resp = await nc.dav("DELETE", path)
        if resp.status_code == 404:
            raise NcError(f"Contact {filename} not found in {addressbook}", 404)
        nc._check_dav(resp, "Deleting contact")
        return {"deleted": filename, "addressbook": addressbook}
