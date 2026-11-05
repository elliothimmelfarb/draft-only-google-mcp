"""OAuth login and token storage. Tokens live in the macOS Keychain, never on disk."""

import base64
import json
import os
import re
import subprocess
import sys

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials

# Optional. When set, login refuses any other Google account.
ACCOUNT = os.environ.get("DRAFT_ONLY_GOOGLE_ACCOUNT", "").strip()
KEYCHAIN_SERVICE = os.environ.get("DRAFT_ONLY_GOOGLE_KEYCHAIN_SERVICE", "draft-only-google-mcp")
KEYCHAIN_ACCOUNT = ACCOUNT or "default"

# gmail.modify covers reading, labels and drafts. No Google scope allows drafts without
# also allowing send, so "never send" is enforced by this server exposing no send tool.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
]


def check_account(actual: str, expected: str = ACCOUNT) -> None:
    if expected and actual.strip().lower() != expected.strip().lower():
        raise PermissionError(f"Signed in as {actual}, but this server is only for {expected}.")


def _keychain_names() -> tuple[str, str]:
    if sys.platform != "darwin":
        raise RuntimeError("This server stores tokens in the macOS Keychain, so it only runs on macOS.")
    for name in (KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT):
        if not re.fullmatch(r"[\w.@+-]+", name):
            raise ValueError(f"Unsupported character in Keychain name: {name!r}")
    return KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT


def _keychain_set(value: str) -> None:
    service, account = _keychain_names()
    # Fed through stdin so the secret never appears in the process list.
    encoded = base64.b64encode(value.encode()).decode()
    cmd = f'add-generic-password -U -s "{service}" -a "{account}" -w "{encoded}"\n'
    subprocess.run(["security", "-i"], input=cmd, text=True, check=True, capture_output=True)


def _keychain_get() -> str | None:
    service, account = _keychain_names()
    r = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    return base64.b64decode(r.stdout.strip()).decode()


def login(client_secret_path: str) -> str:
    from google_auth_oauthlib.flow import InstalledAppFlow

    _keychain_names()  # Fail before the browser opens, not after.
    with open(client_secret_path) as f:
        client_config = json.load(f)
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    hint = {"login_hint": ACCOUNT} if ACCOUNT else {}
    # port=0 picks a free port on 127.0.0.1 for the one-time redirect.
    creds = flow.run_local_server(
        host="127.0.0.1", port=0, open_browser=True, prompt="consent", access_type="offline", **hint,
    )
    email = AuthorizedSession(creds).get(
        "https://gmail.googleapis.com/gmail/v1/users/me/profile"
    ).json()["emailAddress"]
    check_account(email)
    _keychain_set(creds.to_json())
    return email


def session() -> AuthorizedSession:
    stored = _keychain_get()
    if not stored:
        raise RuntimeError("Not signed in. Run: draft-only-google-mcp login <client_secret.json>")
    creds = Credentials.from_authorized_user_info(json.loads(stored), SCOPES)
    return AuthorizedSession(creds)
