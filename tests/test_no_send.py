"""The guarantee this repo exists for. If a test here fails, remove what you added; do not edit the test."""

import asyncio
import re
from pathlib import Path

import draft_only_google_mcp
from draft_only_google_mcp import auth, server

SOURCE = sorted(p for p in Path(draft_only_google_mcp.__file__).parent.rglob("*") if p.suffix == ".py")

# Gmail's send endpoints, send-capable scopes other than the one we need, the full-mailbox
# scope, SMTP, and Calendar's ways of asking Google to email guests.
FORBIDDEN = re.compile(
    r"/send\b|:send\b|gmail\.send|gmail\.compose|mail\.google\.com/(?!mail/)"
    r"|smtp|sendNotifications|externalOnly",
    re.IGNORECASE,
)

SCOPES = {
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
}

TOOLS = {
    "search_threads", "get_thread", "get_message", "download_attachment", "list_labels",
    "modify_thread_labels", "create_draft", "update_draft", "list_drafts", "list_calendars",
    "list_events", "get_event", "create_event", "update_event",
}


def test_source_has_no_send_endpoint_or_scope():
    assert SOURCE
    hits = [f"{p.name}: {m.group()}" for p in SOURCE for m in FORBIDDEN.finditer(p.read_text())]
    assert hits == []


def test_scopes_are_exactly_the_known_list():
    assert set(auth.SCOPES) == SCOPES


def test_tools_are_exactly_the_known_list_and_none_can_send():
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == TOOLS
    params = [p for t in tools for p in t.input_schema.get("properties", {})]
    assert [p for p in params if re.search("send|notify", p, re.IGNORECASE)] == []


def test_calendar_writes_never_ask_google_to_notify_guests(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_api", lambda method, url, **kw: calls.append(kw) or {})
    server.create_event("Lunch", "2026-01-01", "2026-01-02", attendees=["a@b.com"])
    server.update_event("event-id", attendees=["a@b.com"])
    assert [c["params"]["sendUpdates"] for c in calls] == ["none", "none"]
