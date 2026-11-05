"""MCP server exposing Gmail and Google Calendar. There is deliberately no send tool."""

import base64
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from mcp.server.mcpserver import MCPServer

from . import auth
from .mail import build_raw_message, extract_body, headers_of, list_attachments

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CAL = "https://www.googleapis.com/calendar/v3"
ATTACHMENT_DIR = Path(os.environ.get(
    "DRAFT_ONLY_GOOGLE_ATTACHMENT_DIR", Path.home() / "Downloads" / "draft-only-google-mcp"
)).expanduser()
TIME_ZONE = os.environ.get("DRAFT_ONLY_GOOGLE_TIME_ZONE", "UTC")
DRAFTS_URL = "https://mail.google.com/mail/" + (f"?authuser={auth.ACCOUNT}" if auth.ACCOUNT else "") + "#drafts"
SUMMARY_HEADERS = ["From", "To", "Cc", "Subject", "Date"]

_PLAIN_ID = re.compile(r"[\w@.+=-]+", re.ASCII)
_CALENDAR_ID = re.compile(r"[\w@.+=#-]+", re.ASCII)  # Shared calendars look like en.usa#holiday@group.v.calendar.google.com.

mcp = MCPServer(
    os.environ.get("DRAFT_ONLY_GOOGLE_SERVER_NAME", "draft-only-google"),
    instructions=(
        f"Gmail and Google Calendar for {auth.ACCOUNT or 'the signed-in Google account'}. "
        "Email can only be drafted here, never sent: a person reviews and sends each draft."
    ),
)
_session = None


def _api(method: str, url: str, **kw):
    global _session
    if _session is None:
        _session = auth.session()
    r = _session.request(method, url, timeout=30, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"Google API {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else {}


def _id(value: str, *, calendar: bool = False) -> str:
    """Check a Google id and return it safe for one URL path segment. Every id in a URL goes through
    here, because an id holding `/`, `?` or `..` would steer the request to another endpoint."""
    allowed = _CALENDAR_ID if calendar else _PLAIN_ID
    if not isinstance(value, str) or not allowed.fullmatch(value) or not value.strip("."):
        raise ValueError(f"Refused {value!r}: it is not a Google id.")
    return quote(value, safe="@")


def _summarize(msg: dict) -> dict:
    h = headers_of(msg.get("payload", {}))
    return {
        "id": msg["id"],
        "threadId": msg.get("threadId"),
        "labelIds": msg.get("labelIds", []),
        "snippet": msg.get("snippet"),
        **{k.lower(): h[k] for k in SUMMARY_HEADERS if k in h},
    }


def _full(msg: dict) -> dict:
    payload = msg.get("payload", {})
    return {**_summarize(msg), "body": extract_body(payload), "attachments": list_attachments(payload)}


# ---------- Gmail ----------

@mcp.tool()
def search_threads(query: str = "", max_results: int = 20, page_token: str | None = None) -> dict:
    """Search Gmail threads with Gmail search syntax (from:, to:, subject:, newer_than:7d, is:unread, in:sent, label:...).
    Returns each thread's latest message summary. Use get_thread for full bodies."""
    params = {"q": query, "maxResults": min(max_results, 50)}
    if page_token:
        params["pageToken"] = page_token
    listing = _api("GET", f"{GMAIL}/threads", params=params)

    def fetch(t):
        th = _api("GET", f"{GMAIL}/threads/{_id(t['id'])}",
                  params={"format": "metadata", "metadataHeaders": SUMMARY_HEADERS})
        msgs = th.get("messages", [])
        return {"threadId": th["id"], "messageCount": len(msgs), "latest": _summarize(msgs[-1]) if msgs else None}

    with ThreadPoolExecutor(8) as pool:
        threads = list(pool.map(fetch, listing.get("threads", [])))
    return {"threads": threads, "nextPageToken": listing.get("nextPageToken")}


@mcp.tool()
def get_thread(thread_id: str) -> dict:
    """Get every message in a Gmail thread with headers, plain-text body and attachment list."""
    th = _api("GET", f"{GMAIL}/threads/{_id(thread_id)}", params={"format": "full"})
    return {"threadId": th["id"], "messages": [_full(m) for m in th.get("messages", [])]}


@mcp.tool()
def get_message(message_id: str) -> dict:
    """Get one Gmail message with headers, plain-text body and attachment list."""
    return _full(_api("GET", f"{GMAIL}/messages/{_id(message_id)}", params={"format": "full"}))


@mcp.tool()
def download_attachment(message_id: str, attachment_id: str, filename: str) -> str:
    """Save an attachment to the attachment folder and return the local path."""
    data = _api("GET", f"{GMAIL}/messages/{_id(message_id)}/attachments/{_id(attachment_id)}")["data"]
    ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)
    path = ATTACHMENT_DIR / (Path(filename).name or "attachment")
    path.write_bytes(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))
    return str(path)


@mcp.tool()
def list_labels() -> list[dict]:
    """List Gmail labels with their ids."""
    return [{"id": l["id"], "name": l["name"], "type": l.get("type")}
            for l in _api("GET", f"{GMAIL}/labels").get("labels", [])]


@mcp.tool()
def modify_thread_labels(thread_id: str, add_label_ids: list[str] | None = None,
                         remove_label_ids: list[str] | None = None) -> dict:
    """Add or remove label ids on a thread. Archive = remove INBOX. Mark read = remove UNREAD."""
    th = _api("POST", f"{GMAIL}/threads/{_id(thread_id)}/modify",
              json={"addLabelIds": add_label_ids or [], "removeLabelIds": remove_label_ids or []})
    return {"threadId": th["id"], "ok": True}


def _attachment_roots() -> list[Path]:
    configured = os.environ.get("DRAFT_ONLY_GOOGLE_ATTACHMENT_ROOTS", "")
    return [Path(root) for root in configured.split(os.pathsep) if root.strip()] or [ATTACHMENT_DIR]


def _draft_message(to, body, subject, cc, bcc, reply_to_message_id, attachments=None) -> dict:
    original, thread_id = None, None
    if reply_to_message_id:
        m = _api("GET", f"{GMAIL}/messages/{_id(reply_to_message_id)}",
                 params={"format": "metadata", "metadataHeaders": ["Message-ID", "References", "Subject"]})
        original, thread_id = headers_of(m["payload"]), m["threadId"]
    message = {"raw": build_raw_message(to=to, subject=subject, body=body, cc=cc, bcc=bcc, reply_to=original,
                                         attachments=attachments, attachment_roots=_attachment_roots())}
    if thread_id:
        message["threadId"] = thread_id
    return message


@mcp.tool()
def create_draft(to: str, body: str, subject: str | None = None, cc: str | None = None,
                 bcc: str | None = None, reply_to_message_id: str | None = None,
                 attachments: list[str] | None = None) -> dict:
    """Create a draft in the mailbox (it is NOT sent). For a reply, pass reply_to_message_id
    and leave subject empty to get "Re: <original>" threaded correctly. Write each paragraph as
    one line; lines wrapped by hand are joined so the email fills the reader's window.
    attachments: absolute paths of local files to attach. Each must be a regular file inside the
    attachment folder or DRAFT_ONLY_GOOGLE_ATTACHMENT_ROOTS, and together they must fit in 25 MB."""
    d = _api("POST", f"{GMAIL}/drafts",
             json={"message": _draft_message(to, body, subject, cc, bcc, reply_to_message_id, attachments)})
    return {"draftId": d["id"], "messageId": d["message"]["id"], "threadId": d["message"].get("threadId"),
            "url": DRAFTS_URL}


@mcp.tool()
def update_draft(draft_id: str, to: str, body: str, subject: str | None = None, cc: str | None = None,
                 bcc: str | None = None, reply_to_message_id: str | None = None) -> dict:
    """Replace the contents of an existing draft."""
    d = _api("PUT", f"{GMAIL}/drafts/{_id(draft_id)}",
             json={"message": _draft_message(to, body, subject, cc, bcc, reply_to_message_id)})
    return {"draftId": d["id"], "messageId": d["message"]["id"]}


@mcp.tool()
def list_drafts(max_results: int = 20) -> list[dict]:
    """List drafts in the mailbox."""
    drafts = _api("GET", f"{GMAIL}/drafts", params={"maxResults": min(max_results, 50)}).get("drafts", [])

    def fetch(d):
        full = _api("GET", f"{GMAIL}/drafts/{_id(d['id'])}", params={"format": "full"})
        return {"draftId": d["id"], **_full(full["message"])}

    with ThreadPoolExecutor(8) as pool:
        return list(pool.map(fetch, drafts))


# ---------- Calendar ----------

def _event(e: dict) -> dict:
    keep = ["id", "status", "summary", "description", "location", "start", "end", "htmlLink",
            "organizer", "hangoutLink", "recurringEventId"]
    out = {k: e[k] for k in keep if k in e}
    if "attendees" in e:
        out["attendees"] = [{"email": a.get("email"), "responseStatus": a.get("responseStatus"),
                             "self": a.get("self", False)} for a in e["attendees"]]
    return out


@mcp.tool()
def list_calendars() -> list[dict]:
    """List calendars visible to the account."""
    return [{"id": c["id"], "summary": c.get("summary"), "primary": c.get("primary", False),
             "accessRole": c.get("accessRole")}
            for c in _api("GET", f"{CAL}/users/me/calendarList").get("items", [])]


@mcp.tool()
def list_events(time_min: str, time_max: str, calendar_id: str = "primary", query: str | None = None,
                max_results: int = 50) -> list[dict]:
    """List calendar events between two RFC 3339 times (e.g. 2026-01-15T00:00:00+01:00).
    Recurring events are expanded into single instances, ordered by start time."""
    params = {"timeMin": time_min, "timeMax": time_max, "singleEvents": "true", "orderBy": "startTime",
              "maxResults": min(max_results, 250)}
    if query:
        params["q"] = query
    return [_event(e) for e in _api("GET", f"{CAL}/calendars/{_id(calendar_id, calendar=True)}/events", params=params).get("items", [])]


@mcp.tool()
def get_event(event_id: str, calendar_id: str = "primary") -> dict:
    """Get one calendar event."""
    return _event(_api("GET", f"{CAL}/calendars/{_id(calendar_id, calendar=True)}/events/{_id(event_id)}"))


def _check_attendees(attendees: list[str] | None) -> None:
    allowed = os.environ.get("DRAFT_ONLY_GOOGLE_ALLOW_ATTENDEES", "").strip().lower() in ("1", "true", "yes", "on")
    if attendees is not None and not allowed:
        raise PermissionError("Adding guests is off, because Google may email them. "
                              "Leave out attendees, or set DRAFT_ONLY_GOOGLE_ALLOW_ATTENDEES=1 to turn it on.")


def _when(value: str) -> dict:
    return {"date": value} if len(value) == 10 else {"dateTime": value}


@mcp.tool()
def create_event(summary: str, start: str, end: str, calendar_id: str = "primary",
                 description: str | None = None, location: str | None = None,
                 attendees: list[str] | None = None, time_zone: str = TIME_ZONE) -> dict:
    """Create a calendar event. start/end are RFC 3339 datetimes or YYYY-MM-DD for all-day.
    attendees is refused unless DRAFT_ONLY_GOOGLE_ALLOW_ATTENDEES is on. This server asks Google
    not to notify guests, but the event may still appear on their calendars and Google may still
    email them, so treat adding a guest as a visible action."""
    _check_attendees(attendees or None)
    body = {"summary": summary, "start": {**_when(start), "timeZone": time_zone},
            "end": {**_when(end), "timeZone": time_zone}}
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees]
    e = _api("POST", f"{CAL}/calendars/{_id(calendar_id, calendar=True)}/events",
             params={"sendUpdates": "none"}, json=body)
    return _event(e)


@mcp.tool()
def update_event(event_id: str, calendar_id: str = "primary", summary: str | None = None,
                 start: str | None = None, end: str | None = None, description: str | None = None,
                 location: str | None = None, attendees: list[str] | None = None,
                 time_zone: str = TIME_ZONE) -> dict:
    """Change fields on a calendar event; omitted fields stay as they are.
    attendees replaces the whole list and is refused unless DRAFT_ONLY_GOOGLE_ALLOW_ATTENDEES is on.
    This server asks Google not to notify guests, but Google may still email them about a change."""
    _check_attendees(attendees)
    body = {}
    if summary is not None:
        body["summary"] = summary
    if start:
        body["start"] = {**_when(start), "timeZone": time_zone}
    if end:
        body["end"] = {**_when(end), "timeZone": time_zone}
    if description is not None:
        body["description"] = description
    if location is not None:
        body["location"] = location
    if attendees is not None:
        body["attendees"] = [{"email": a} for a in attendees]
    e = _api("PATCH", f"{CAL}/calendars/{_id(calendar_id, calendar=True)}/events/{_id(event_id)}",
             params={"sendUpdates": "none"}, json=body)
    return _event(e)


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["login"] and len(args) == 2:
        print(f"Signed in as {auth.login(args[1])}. Token stored in the macOS Keychain.")
    elif args[:1] == ["whoami"]:
        print(_api("GET", f"{GMAIL}/profile")["emailAddress"])
    elif not args:
        mcp.run("stdio")
    else:
        sys.exit("usage: draft-only-google-mcp [login <client_secret.json> | whoami]")
