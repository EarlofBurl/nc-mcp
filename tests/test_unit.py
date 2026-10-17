"""Offline unit tests (no network required)."""

import pytest

from nc_mcp.auth import BearerTokenMiddleware
from nc_mcp.config import Config
from nc_mcp.dt import DateTimeError, parse_dt
from nc_mcp.modules.contacts import _build_vcard, _contact, _vcard_field
from nc_mcp.modules.files import _display_path


class TestDisplayPath:
    def test_root(self):
        assert _display_path("/remote.php/dav/files/manuel/") == "/"

    def test_file(self):
        assert _display_path("/remote.php/dav/files/manuel/Documents/todo.md") == "/Documents/todo.md"

    def test_urlencoded(self):
        assert _display_path("/remote.php/dav/files/manuel/Dokumente/Datei%20mit%20Leerzeichen.txt") == (
            "/Dokumente/Datei mit Leerzeichen.txt"
        )


class TestParseDt:
    def test_date_only(self):
        cfg = Config()
        result = parse_dt("2025-12-24", cfg)
        assert result.year == 2025 and result.month == 12 and result.day == 24

    def test_naive_datetime_gets_default_tz(self):
        cfg = Config()
        result = parse_dt("2025-12-24T18:00", cfg)
        assert result.tzinfo is not None

    def test_offset_is_kept(self):
        cfg = Config()
        result = parse_dt("2025-12-24T18:00:00+02:00", cfg)
        assert result.utcoffset().total_seconds() == 7200

    def test_invalid(self):
        with pytest.raises(DateTimeError):
            parse_dt("not-a-date", Config())


class TestVcard:
    def test_build_and_parse(self):
        vcard = _build_vcard("Max Mustermann", ["max@example.com"], ["+49 170 1234567"], "ACME GmbH")
        assert "BEGIN:VCARD" in vcard and "VERSION:3.0" in vcard
        contact = _contact("max.vcf", vcard)
        assert contact["fn"] == "Max Mustermann"
        assert contact["emails"] == ["max@example.com"]
        assert contact["phones"] == ["+49 170 1234567"]
        assert contact["org"] == "ACME GmbH"

    def test_field_extraction(self):
        vcard = "BEGIN:VCARD\r\nFN: Erika Muster \r\nEMAIL;TYPE=INTERNET:erika@example.com\r\nEND:VCARD"
        assert _vcard_field(vcard, "FN") == ["Erika Muster"]
        assert _vcard_field(vcard, "EMAIL") == ["erika@example.com"]
        assert _vcard_field(vcard, "TEL") == []


class TestAuthMiddleware:
    @pytest.mark.asyncio
    async def test_missing_token_rejects(self):
        from starlette.testclient import TestClient

        async def inner_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"ok"})

        wrapped = BearerTokenMiddleware(inner_app, "secret-token")
        client = TestClient(wrapped)
        assert client.get("/").status_code == 401
        assert client.get("/", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get("/", headers={"Authorization": "Bearer secret-token"}).status_code == 200


class TestMailHelpers:
    def test_recipients_single_string(self):
        from nc_mcp.modules.mail import _recipients

        assert _recipients("a@b.de") == [{"email": "a@b.de", "label": "a@b.de"}]

    def test_recipients_list(self):
        from nc_mcp.modules.mail import _recipients

        result = _recipients(["x@y.de", " z@w.de "])
        assert result == [{"email": "x@y.de", "label": "x@y.de"}, {"email": "z@w.de", "label": "z@w.de"}]

    def test_recipients_invalid(self):
        from nc_mcp.modules.mail import _recipients
        from nc_mcp.nc_client import NcError

        with pytest.raises(NcError):
            _recipients(["kein-email"])
        assert _recipients([]) == []  # leer ist gültig (cc/bcc)

    def test_strip_html(self):
        from nc_mcp.modules.mail import _strip_html

        assert _strip_html("plain text") == "plain text"
        assert "Zeile1" in _strip_html("<p>Zeile1</p><p>Zeile2</p>")
        assert "Zeile2" in _strip_html("<p>Zeile1</p><p>Zeile2</p>")
        assert _strip_html("<b>fett</b>") == "fett"


class TestLocalIso:
    def test_utc_converted_to_local(self):
        from datetime import UTC, datetime
        from zoneinfo import ZoneInfo

        from nc_mcp.dt import local_iso

        dt = datetime(2026, 10, 19, 7, 50, tzinfo=UTC)
        result = local_iso(dt)  # Europe/Berlin (+02:00 im Oktober)
        assert "+02:00" in result
        assert result.startswith("2026-10-19T09:50")

    def test_naive_gets_local_tz(self, monkeypatch):
        from datetime import datetime

        from nc_mcp.dt import local_iso

        monkeypatch.setenv("NC_DEFAULT_TZ", "Europe/Berlin")
        result = local_iso(datetime(2026, 10, 19, 9, 50))
        assert "+02:00" in result
