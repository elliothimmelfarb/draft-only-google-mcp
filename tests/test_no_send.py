"""The guarantee this repo exists for. If a test here fails, remove what you added; do not edit the test."""

import asyncio
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

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
    monkeypatch.setenv("DRAFT_ONLY_GOOGLE_ALLOW_ATTENDEES", "1")
    calls = []
    monkeypatch.setattr(server, "_api", lambda method, url, **kw: calls.append(kw) or {})
    server.create_event("Lunch", "2026-01-01", "2026-01-02", attendees=["a@b.com"])
    server.update_event("event-id", attendees=["a@b.com"])
    assert [c["params"]["sendUpdates"] for c in calls] == ["none", "none"]


def _tools():
    return asyncio.run(server.mcp.list_tools())


def _required(tool):
    return {name: "abc" for name in tool.input_schema.get("required", [])}


ID_PARAMS = sorted((t.name, p) for t in _tools() for p in t.input_schema.get("properties", {}) if p.endswith("_id"))
HOSTILE = ["../drafts/send?", "../messages/send?", "../messages/abc/trash?", "..", ".", "", "a/b", "a?b",
           "a%2fb", "a b", "a\n", "primary/events?sendUpdates=all&x=", "@evil.example/"]


def test_every_id_parameter_is_covered():
    assert len(ID_PARAMS) == 14


@pytest.mark.parametrize("tool,param", ID_PARAMS)
@pytest.mark.parametrize("bad", HOSTILE)
def test_no_tool_lets_an_id_steer_the_url(google, tool, param, bad):
    if param == "reply_to_message_id" and not bad:
        return  # An empty reply id just means this draft is not a reply, so no URL is built from it.
    kwargs = {**_required(next(t for t in _tools() if t.name == tool)), param: bad}
    with pytest.raises(ValueError):
        getattr(server, tool)(**kwargs)
    assert google.urls == []


@pytest.mark.parametrize("tool,param", [p for p in ID_PARAMS if p[1] != "calendar_id"])
def test_only_calendar_ids_may_hold_a_hash(google, tool, param):
    kwargs = {**_required(next(t for t in _tools() if t.name == tool)), param: "a#b"}
    with pytest.raises(ValueError):
        getattr(server, tool)(**kwargs)


def test_final_urls_stay_on_read_and_draft_endpoints(google, monkeypatch):
    monkeypatch.setenv("DRAFT_ONLY_GOOGLE_ALLOW_ATTENDEES", "1")
    for tool in _tools():
        getattr(server, tool.name)(**_required(tool))
    server.create_event("Lunch", "2026-01-01", "2026-01-02", attendees=["a@b.com"])
    server.update_event("abc", attendees=["a@b.com"])
    assert google.urls
    for url in google.urls:
        parts = urlsplit(url)
        assert parts.scheme == "https" and parts.netloc in {"gmail.googleapis.com", "www.googleapis.com"}, url
        segments = parts.path.split("/")
        assert ".." not in segments and "send" not in segments and "trash" not in segments, url
        query = parse_qs(parts.query)
        assert query.get("sendUpdates", ["none"]) == ["none"], url
        assert "sendNotifications" not in query, url


def test_attendees_are_refused_unless_turned_on(google, monkeypatch):
    monkeypatch.delenv("DRAFT_ONLY_GOOGLE_ALLOW_ATTENDEES", raising=False)
    for call in (lambda: server.create_event("Lunch", "2026-01-01", "2026-01-02", attendees=["a@b.com"]),
                 lambda: server.update_event("abc", attendees=["a@b.com"]),
                 lambda: server.update_event("abc", attendees=[])):
        with pytest.raises(PermissionError, match="DRAFT_ONLY_GOOGLE_ALLOW_ATTENDEES"):
            call()
    assert google.urls == []


def test_setting_off_values_do_not_allow_attendees(google, monkeypatch):
    monkeypatch.setenv("DRAFT_ONLY_GOOGLE_ALLOW_ATTENDEES", "0")
    with pytest.raises(PermissionError):
        server.update_event("abc", attendees=["a@b.com"])


def test_calendar_docs_do_not_promise_that_guests_stay_unaware():
    text = " ".join(t.description for t in _tools() if t.name in ("create_event", "update_event"))
    assert "never notified" not in text
    assert "may still" in text
