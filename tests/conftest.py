"""Every test here runs against a fake session, so nothing reaches Google or the Keychain."""

import pytest
import requests

from draft_only_google_mcp import server

PAYLOAD = {"id": "abc", "threadId": "abc", "message": {"id": "abc"}, "data": "aGk", "payload": {}, "messages": []}


class FakeResponse:
    status_code = 200
    content = b"{}"
    text = "{}"

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeSession:
    """Builds the URL the way requests would, records it and never dispatches it."""

    def __init__(self):
        self.urls = []
        self.payload = PAYLOAD

    def request(self, method, url, timeout=None, **kw):
        prepared = requests.Request(method, url, params=kw.get("params"), json=kw.get("json")).prepare()
        self.urls.append(prepared.url)
        return FakeResponse(self.payload)


@pytest.fixture
def google(monkeypatch, tmp_path):
    fake = FakeSession()
    monkeypatch.setattr(server, "_session", fake)
    monkeypatch.setattr(server, "ATTACHMENT_DIR", tmp_path)
    return fake
