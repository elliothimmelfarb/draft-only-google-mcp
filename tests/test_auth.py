from types import SimpleNamespace

from draft_only_google_mcp import auth, server


def test_keychain_calls_use_the_absolute_path(monkeypatch):
    # A planted `security` earlier on PATH would otherwise receive the token.
    calls = []
    monkeypatch.setattr(auth.sys, "platform", "darwin")
    monkeypatch.setattr(auth.subprocess, "run",
                        lambda argv, **kw: calls.append(argv) or SimpleNamespace(returncode=1, stdout=""))
    auth._keychain_get()
    auth._keychain_set("value")
    assert [argv[0] for argv in calls] == ["/usr/bin/security", "/usr/bin/security"]


def test_list_drafts_shows_bcc_so_a_hidden_recipient_can_be_seen(google):
    google.payload = {"drafts": [{"id": "abc"}],
                      "message": {"id": "abc", "payload": {"headers": [{"name": "Bcc", "value": "hidden@example.com"}]}}}
    assert server.list_drafts()[0]["bcc"] == "hidden@example.com"
