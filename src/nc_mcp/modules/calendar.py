"""MCP tools for the Nextcloud Calendar app (CalDAV)."""

from __future__ import annotations

import urllib.parse
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastmcp import FastMCP
from icalendar import Calendar as ICal, Event
from icalendar.prop import vDate, vDatetime

from ..config import Config
from ..dt import DateTimeError, iso, parse_dt
from ..nc_client import NC, NcError, require_name

REPORT_QUERY = """<?xml version="1.0"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:getetag/>
    <c:calendar-data>{expand_xml}</c:calendar-data>
  </d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="{component}">
{time_range}
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>
"""

TIME_RANGE = '        <c:time-range start="{start}" end="{end}"/>'

# Server-side expansion of recurring events (RFC 4791 §9.6.5: CALDAV:expand is
# a child of CALDAV:calendar-data, NOT of the comp-filter). Without it the
# time-range filter only matches the RRULE's base occurrence (e.g. a weekly
# series started in January would never show up for 'this Monday').
EXPAND = '\n      <c:expand start="{start}" end="{end}"/>'


def _set_datetime_prop(component: Any, name: str, value: datetime | date) -> None:
    """Replace an iCalendar property with a properly serialized date(-time).

    Assigning raw datetime objects via `component[name] = ...` makes icalendar
    serialize the Python repr, which Sabre rejects with HTTP 415.
    """
    component[name] = vDatetime(value) if isinstance(value, datetime) else vDate(value)


def _as_utc(value: datetime | date) -> datetime:
    """Convert a parsed datetime/date to an aware UTC datetime (for time ranges)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.combine(value, datetime.min.time()).replace(tzinfo=UTC)


def _calendar_name(href: str) -> str:
    seg = href.rstrip("/").rsplit("/", 1)[-1]
    return urllib.parse.unquote(seg)


def _filename(href: str) -> str:
    return urllib.parse.unquote(href.rstrip("/").rsplit("/", 1)[-1])


def _parse_calendar_data(text: str, component: str) -> list[Any]:
    try:
        ical = ICal.from_ical(text)
    except ValueError:
        return []
    return [sub for sub in ical.walk(component) if getattr(sub, "name", None) == component]


def _dt_value(prop: Any) -> str | None:
    if prop is None:
        return None
    value = prop.dt if hasattr(prop, "dt") else prop
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return f"{value.isoformat()} (floating)"
        return value.isoformat()
    return iso(value)


def _entry(filename: str, component: Any) -> dict[str, Any]:
    rec_id = _dt_value(component.get("recurrence-id"))
    rrule = str(component.get("rrule", "")) or None
    return {
        "filename": filename,
        "uid": str(component.get("uid", "")),
        "summary": str(component.get("summary", "")),
        "start": _dt_value(component.get("dtstart")),
        "end": _dt_value(component.get("dtend")),
        "all_day": not isinstance(getattr(component.get("dtstart"), "dt", None), datetime),
        "location": str(component.get("location", "")) or None,
        "description": str(component.get("description", "")) or None,
        "status": str(component.get("status", "")) or None,
        # Present when the server expanded a recurring series into this
        # single occurrence. `filename` still points to the series master.
        "recurrence_id": rec_id,
        "recurring": bool(rec_id or rrule),
        "rrule": rrule,
    }


async def _list_calendars(nc: NC) -> list[dict[str, Any]]:
    entries = await nc.propfind(f"/remote.php/dav/calendars/{nc.username}/", depth=1)
    calendars = []
    for e in entries:
        if not e.get("is_dir"):
            continue
        calendars.append(
            {
                "name": _calendar_name(e["href"]),
                "display_name": e.get("displayname") or _calendar_name(e["href"]),
                "color": (e.get("calendar-color") or "").strip() or None,
            }
        )
    return calendars


async def _report(
    nc: NC,
    calendar: str,
    component: str,
    start: datetime | None,
    end: datetime | None,
    entry_builder: Any = None,
) -> list[dict[str, Any]]:
    range_xml = ""
    expand_xml = ""
    if start is not None and end is not None:
        fmt_start = start.strftime("%Y%m%dT%H%M%SZ")
        fmt_end = end.strftime("%Y%m%dT%H%M%SZ")
        range_xml = TIME_RANGE.format(start=fmt_start, end=fmt_end)
        if component == "VEVENT":
            expand_xml = EXPAND.format(start=fmt_start, end=fmt_end)
    body = REPORT_QUERY.format(
        component=component, time_range=range_xml, expand_xml=expand_xml
    )
    path = nc.dav_calendar_path(calendar)
    raw = await nc.dav("REPORT", path, body, {"Depth": "1"})
    if raw.status_code == 404:
        raise NcError(f"Calendar '{calendar}' not found. Check calendar_list.", 404)
    nc._check_dav(raw, f"REPORT on {calendar}")
    from ..nc_client import parse_multi_status

    results = []
    seen: set[tuple[str, str]] = set()
    for e in parse_multi_status(raw.text):
        data = e.get("calendar-data")
        if not data:
            continue
        builder = entry_builder or _entry
        for comp in _parse_calendar_data(data, component):
            entry = builder(_filename(e["href"]), comp)
            # Expanded occurrences can come back multiple times when a calendar
            # contains duplicated master objects; serve each occurrence once.
            key = (entry.get("summary", ""), str(entry.get("start")))
            if key in seen:
                continue
            seen.add(key)
            results.append(entry)
    return results


def register(mcp: FastMCP, nc: NC, cfg: Config) -> None:

    @mcp.tool
    async def calendar_list() -> list[dict[str, Any]]:
        """List the user's calendars (also usable as task lists)."""
        return await _list_calendars(nc)

    @mcp.tool
    async def calendar_events_list(
        calendar: str,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]]:
        """List events of a calendar, optionally within a time range.

        Args:
            calendar: Calendar name (see calendar_list), e.g. "personal".
            start: Range start as ISO datetime (default: now).
            end: Range end as ISO datetime (default: now + 31 days).
            Times are interpreted in the server's default timezone when no
            offset is given, e.g. "2025-12-24T18:00" or "2025-12-24T18:00:00+01:00".
        """
        try:
            start_utc = _as_utc(parse_dt(start, cfg)) if start else datetime.now(UTC)
            end_utc = _as_utc(parse_dt(end, cfg)) if end else datetime.now(UTC) + timedelta(days=31)
        except DateTimeError as exc:
            raise NcError(str(exc)) from exc
        events = await _report(nc, calendar, "VEVENT", start_utc, end_utc)
        return sorted(events, key=lambda e: (e["start"] or ""))

    @mcp.tool
    async def calendar_event_create(
        calendar: str,
        summary: str,
        dtstart: str,
        dtend: str,
        description: str = "",
        location: str = "",
    ) -> dict[str, Any]:
        """Create an event in a calendar.

        Args:
            calendar: Calendar name (see calendar_list).
            summary: Event title.
            dtstart: Start, e.g. "2025-12-24T18:00" (with tz) or "2025-12-24" (all-day).
            dtend: End. For all-day events the end date is exclusive.
            description: Optional description.
            location: Optional location.
        """
        try:
            start = parse_dt(dtstart, cfg)
            end = parse_dt(dtend, cfg)
        except DateTimeError as exc:
            raise NcError(str(exc)) from exc

        calendar = require_name(calendar, "calendar")
        ical = ICal()
        ical.add("prodid", "-//nc-mcp//EN")
        ical.add("version", "2.0")
        event = Event()
        uid = str(uuid.uuid4())
        event.add("uid", uid)
        event.add("summary", summary)
        event.add("dtstart", start)
        if end is not None:
            event.add("dtend", end)
        event.add("dtstamp", datetime.now(UTC))
        if description:
            event.add("description", description)
        if location:
            event.add("location", location)
        ical.add_component(event)

        filename = f"{uid}.ics"
        headers = {"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"}
        resp = await nc.dav(
            "PUT", nc.dav_calendar_path(calendar) + urllib.parse.quote(filename), ical.to_ical(), headers
        )
        if resp.status_code == 404:
            raise NcError(f"Calendar '{calendar}' not found. Check calendar_list.", 404)
        if resp.status_code in (403, 409, 415):
            raise NcError(f"Calendar '{calendar}' rejected the event (HTTP {resp.status_code}): {resp.text[:200]}", resp.status_code)
        nc._check_dav(resp, "Creating event")
        return {"created": summary, "calendar": calendar, "uid": uid, "filename": filename}

    @mcp.tool
    async def calendar_event_update(
        calendar: str,
        filename: str,
        summary: str | None = None,
        dtstart: str | None = None,
        dtend: str | None = None,
        description: str | None = None,
        location: str | None = None,
    ) -> dict[str, Any]:
        """Update fields of an existing event. Only provided fields are changed.

        For recurring events the filename refers to the series master: changing
        summary/description/dates changes the whole series.

        Args:
            calendar: Calendar name.
            filename: Filename of the event (from calendar_events_list, e.g. "abc.ics").
            summary, dtstart, dtend, description, location: New values (optional).
        """
        calendar = require_name(calendar, "calendar")
        filename = require_name(filename, "filename")
        path = nc.dav_calendar_path(calendar) + urllib.parse.quote(filename)
        resp = await nc.dav("GET", path)
        if resp.status_code == 404:
            raise NcError(f"Event {filename} not found in calendar {calendar}", 404)
        nc._check_dav(resp, "Reading event")
        try:
            ical = ICal.from_ical(resp.text)
        except ValueError as exc:
            raise NcError(f"Could not parse event data: {exc}") from exc
        etag = resp.headers.get("etag")
        event = None
        for sub in ical.walk("VEVENT"):
            event = sub
            break
        if event is None:
            raise NcError("Event object contains no VEVENT")

        try:
            if summary is not None:
                event["summary"] = summary
            if description is not None:
                event["description"] = description
            if location is not None:
                event["location"] = location
            if dtstart is not None:
                _set_datetime_prop(event, "dtstart", parse_dt(dtstart, cfg))
            if dtend is not None:
                _set_datetime_prop(event, "dtend", parse_dt(dtend, cfg))
        except DateTimeError as exc:
            raise NcError(str(exc)) from exc

        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        if etag:
            headers["If-Match"] = etag
        put = await nc.dav("PUT", path, ical.to_ical(), headers)
        nc._check_dav(put, "Updating event")
        return {"updated": filename, "calendar": calendar}

    @mcp.tool
    async def calendar_event_delete(calendar: str, filename: str) -> dict[str, Any]:
        """Delete a single (non-recurring) event from a calendar.

        For recurring events this tool refuses: the filename points to the
        series master, so deleting it would remove ALL occurrences. Use
        calendar_event_delete_series if that is really intended.

        Args:
            calendar: Calendar name.
            filename: Filename of the event (from calendar_events_list).
        """
        calendar = require_name(calendar, "calendar")
        filename = require_name(filename, "filename")
        path = nc.dav_calendar_path(calendar) + urllib.parse.quote(filename)
        head = await nc.dav("GET", path)
        if head.status_code == 404:
            raise NcError(f"Event {filename} not found in calendar {calendar}", 404)
        nc._check_dav(head, "Reading event")
        if "RRULE:" in head.text:
            raise NcError(
                f"{filename} is a recurring series — deleting the file would remove "
                "ALL occurrences. If you really want to delete the entire series, "
                "use calendar_event_delete_series (ideally after confirming with "
                "the user)."
            )
        resp = await nc.dav("DELETE", path)
        nc._check_dav(resp, "Deleting event")
        return {"deleted": filename, "calendar": calendar}

    @mcp.tool
    async def calendar_event_delete_series(calendar: str, filename: str) -> dict[str, Any]:
        """Delete an entire recurring event series (ALL its occurrences).

        Only use this when the user explicitly wants the whole series gone —
        for a single event use calendar_event_delete instead.

        Args:
            calendar: Calendar name.
            filename: Filename of the series master (from calendar_events_list).
        """
        calendar = require_name(calendar, "calendar")
        filename = require_name(filename, "filename")
        path = nc.dav_calendar_path(calendar) + urllib.parse.quote(filename)
        resp = await nc.dav("DELETE", path)
        if resp.status_code == 404:
            raise NcError(f"Event {filename} not found in calendar {calendar}", 404)
        nc._check_dav(resp, "Deleting series")
        return {"deleted_series": filename, "calendar": calendar}
