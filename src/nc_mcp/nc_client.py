"""Async Nextcloud client used by the MCP tool modules.

Talks to the OCS JSON API, WebDAV (files), CalDAV (calendars/tasks) and
CardDAV (contacts) endpoints of a Nextcloud server using an app password.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import httpx

NS = {
    "d": "DAV:",
    "caldav": "urn:ietf:params:xml:ns:caldav",
    "carddav": "urn:ietf:params:xml:ns:carddav",
    "oc": "http://owncloud.org/ns",
}


class NcError(Exception):
    """Error raised for all failed Nextcloud API calls."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _xml(body: str) -> ET.Element:
    return ET.fromstring(body)


def require_path(value: str, name: str) -> str:
    """Validate a user-supplied DAV path argument for write/delete tools.

    Rejects empty values and bare roots so an LLM can never truncate the
    path down to a whole collection (e.g. deleting the entire file root).
    """
    cleaned = (value or "").strip()
    if not cleaned or cleaned.strip("/") == "":
        raise NcError(f"Invalid '{name}': must be a non-empty path below the root")
    return cleaned


def require_name(value: str, name: str) -> str:
    """Validate a single name argument (calendar, addressbook, filename, token)."""
    cleaned = (value or "").strip()
    if not cleaned or "/" in cleaned:
        raise NcError(f"Invalid '{name}': must be a single non-empty name without '/'")
    return cleaned


def _status_ok(status_text: str | None) -> bool:
    return status_text is not None and " 200 " in status_text + " "


def parse_multi_status(body: str) -> list[dict[str, Any]]:
    """Parse a DAV multistatus PROPFIND/REPORT response into plain dicts."""
    root = _xml(body)
    results: list[dict[str, Any]] = []
    for resp in root.findall("d:response", NS):
        entry: dict[str, Any] = {"href": resp.findtext("d:href", "", NS)}
        for propstat in resp.findall("d:propstat", NS):
            if not _status_ok(propstat.findtext("d:status", None, NS)):
                continue
            prop = propstat.find("d:prop", NS)
            if prop is None:
                continue
            for child in prop:
                tag = child.tag.split("}", 1)[-1]
                if tag == "resourcetype":
                    entry["is_dir"] = child.find("d:collection", NS) is not None
                elif len(child) == 0 and child.text is not None:
                    entry[tag] = child.text
                else:
                    entry[tag] = None
        results.append(entry)
    return results


class NC:
    """Thin async wrapper around the Nextcloud HTTP APIs."""

    def __init__(
        self,
        base_url: str,
        username: str,
        app_password: str,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self._client = httpx.AsyncClient(
            auth=(username, app_password),
            timeout=timeout,
            follow_redirects=True,
        )
        # Discard Set-Cookie headers after every response: Nextcloud sets PHP
        # session cookies on OCS responses; sending those back on a DAV request
        # makes Sabre's CSRF protection reject it (401 'CSRF check not passed').
        # An API client with app-password auth has no use for cookies.
        self._client.event_hooks["response"].append(self._drop_cookies)

    async def _drop_cookies(self, response: httpx.Response) -> None:
        self._client.cookies.clear()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------------------------------------------------------------- OCS
    async def ocs(
        self,
        method: str,
        path: str,
        json_data: Any | None = None,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Call an OCS endpoint and return the `data` payload."""
        url = f"{self.base_url}{path}"
        try:
            resp = await self._client.request(
                method,
                url,
                json=json_data,
                data=data,
                params=params,
                headers={"Accept": "application/json", "OCS-APIRequest": "true"},
            )
        except httpx.HTTPError as exc:
            raise NcError(f"Nextcloud request failed: {exc}") from exc
        if resp.status_code in (401,):
            raise NcError("Authentication failed (401). Check NC_USERNAME / NC_APP_PASSWORD.", 401)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise NcError(
                f"Non-JSON OCS response (HTTP {resp.status_code}): {resp.text[:300]!r}",
                resp.status_code,
            ) from exc
        meta = payload.get("ocs", {}).get("meta", {})
        statuscode = meta.get("statuscode", resp.status_code)
        if statuscode not in (100, 200, 201, 202):
            raise NcError(
                f"OCS error {statuscode}: {meta.get('message', 'unknown error')}",
                statuscode,
            )
        return payload.get("ocs", {}).get("data")

    # ---------------------------------------------------------------- REST
    async def rest(
        self,
        method: str,
        path: str,
        json_data: Any | None = None,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        """Call a plain (non-OCS) JSON endpoint, e.g. the Notes REST API."""
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        try:
            resp = await self._client.request(
                method, url, json=json_data, params=params, headers=headers
            )
        except httpx.HTTPError as exc:
            raise NcError(f"Nextcloud request failed: {exc}") from exc
        if resp.status_code == 401:
            raise NcError("Authentication failed (401). Check NC_USERNAME / NC_APP_PASSWORD.", 401)
        if resp.status_code == 404:
            raise NcError(f"Not found: {path}", 404)
        if resp.status_code >= 400:
            raise NcError(f"{method} {path} failed (HTTP {resp.status_code}): {resp.text[:200]}", resp.status_code)
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # ---------------------------------------------------------------- DAV
    async def dav(
        self,
        method: str,
        path: str,
        body: bytes | str | None = None,
        headers: dict[str, str] | None = None,
        content_type: str = "application/xml",
    ) -> httpx.Response:
        """Raw DAV request (PROPFIND, REPORT, GET, PUT, DELETE, MKCOL, MOVE...)."""
        url = f"{self.base_url}{path}"
        hdrs = {"Content-Type": content_type}
        if headers:
            hdrs.update(headers)
        try:
            return await self._client.request(method, url, content=body, headers=hdrs)
        except httpx.HTTPError as exc:
            raise NcError(f"Nextcloud DAV request failed: {exc}") from exc

    @staticmethod
    def _check_dav(resp: httpx.Response, what: str) -> httpx.Response:
        if resp.status_code >= 400:
            raise NcError(f"{what} failed (HTTP {resp.status_code}): {resp.text[:300]}", resp.status_code)
        return resp

    async def propfind(self, path: str, depth: int = 1) -> list[dict[str, Any]]:
        body = (
            '<?xml version="1.0"?>'
            '<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
            "<d:prop>"
            "<d:resourcetype/><d:displayname/><d:getcontentlength/>"
            "<d:getcontenttype/><d:getlastmodified/><d:getetag/>"
            "<oc:fileid/><oc:permissions/>"
            "</d:prop></d:propfind>"
        )
        resp = await self.dav("PROPFIND", path, body, {"Depth": str(depth)})
        self._check_dav(resp, "PROPFIND")
        return parse_multi_status(resp.text)

    async def report(self, path: str, query_body: str) -> list[dict[str, Any]]:
        resp = await self.dav("REPORT", path, query_body, {"Depth": "1"})
        self._check_dav(resp, "REPORT")
        return parse_multi_status(resp.text)

    # ---------------------------------------------------------- path helpers
    def dav_file_url_path(self, path: str) -> str:
        from urllib.parse import quote

        clean = path.strip("/")
        return f"/remote.php/dav/files/{self.username}/{quote(clean)}"

    def dav_calendar_path(self, calendar: str) -> str:
        from urllib.parse import quote

        return f"/remote.php/dav/calendars/{self.username}/{quote(calendar.strip('/'))}/"

    def dav_addressbook_path(self, addressbook: str) -> str:
        from urllib.parse import quote

        return f"/remote.php/dav/addressbooks/users/{self.username}/{quote(addressbook.strip('/'))}/"
