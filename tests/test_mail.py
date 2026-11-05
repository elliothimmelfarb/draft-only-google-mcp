import base64
import email
import email.policy
import time

import pytest

from draft_only_google_mcp.mail import build_raw_message, extract_body, html_to_text
from draft_only_google_mcp.auth import check_account


def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def decode_raw(raw: str) -> email.message.Message:
    return email.message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))


def test_extract_body_prefers_plain_text_in_nested_multipart():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/html", "body": {"data": b64("<p>Olá html</p>")}},
                    {"mimeType": "text/plain", "body": {"data": b64("Olá plain")}},
                ],
            },
            {"mimeType": "application/pdf", "filename": "a.pdf", "body": {"attachmentId": "x"}},
        ],
    }
    assert extract_body(payload) == "Olá plain"


def test_extract_body_falls_back_to_stripped_html():
    payload = {"mimeType": "text/html", "body": {"data": b64("<div>Hi<br>there</div>")}}
    assert "Hi" in extract_body(payload) and "<" not in extract_body(payload)


def test_reply_keeps_thread_headers_and_prefixes_subject():
    original = {
        "Message-ID": "<abc@mail.gmail.com>",
        "References": "<root@x>",
        "Subject": "Orçamento",
    }
    msg = decode_raw(build_raw_message(to="a@b.com", subject=None, body="Sim", reply_to=original))
    assert msg["In-Reply-To"] == "<abc@mail.gmail.com>"
    assert msg["References"] == "<root@x> <abc@mail.gmail.com>"
    assert str(email.header.make_header(email.header.decode_header(msg["Subject"]))) == "Re: Orçamento"


def test_reply_does_not_double_prefix_subject():
    msg = decode_raw(build_raw_message(to="a@b.com", subject=None, body="x",
                                       reply_to={"Message-ID": "<m>", "Subject": "RE: hi"}))
    assert msg["Subject"] == "RE: hi"


def test_check_account_rejects_wrong_mailbox():
    check_account("Someone@Example.com", "someone@example.com")
    with pytest.raises(PermissionError):
        check_account("other@example.com", "someone@example.com")


def test_check_account_allows_any_mailbox_when_none_is_configured():
    check_account("anyone@example.com", "")


def test_draft_joins_lines_wrapped_by_the_writer():
    body = ("Hi Sam,\n\nThe lighthouse keeper repainted the spiral stairs in a shade\n"
            "of green that nobody on the island had chosen.\n\n"
            "- Lanterns: Seven brass lanterns came back from the polisher on Tuesday.\n"
            "They shine brighter than the harbor lights.\n"
            "- Rope: coiled.\n\nCheers,\nAlex")
    text = _part(decode_raw(build_raw_message(to="a@b.com", subject="s", body=body)), "plain")
    assert text.replace("\r\n", "\n") == (
        "Hi Sam,\n\nThe lighthouse keeper repainted the spiral stairs in a shade "
        "of green that nobody on the island had chosen.\n\n"
        "- Lanterns: Seven brass lanterns came back from the polisher on Tuesday. "
        "They shine brighter than the harbor lights.\n"
        "- Rope: coiled.\n\nCheers,\nAlex\n")


def _part(msg, subtype):
    return next(p for p in msg.walk() if p.get_content_type() == f"text/{subtype}").get_payload(decode=True).decode()


def test_draft_carries_html_so_gmail_does_not_hard_wrap_it():
    # A plain-text-only draft opens in Gmail's plain-text composer, which re-wraps every
    # line at about 70 characters when it saves or sends. An HTML part keeps it in rich mode.
    body = ("Hi Sam,\n\nThe ferry to the island runs twice on Sundays. I've marked the tide table, and the "
            "afternoon crossing looks calmer.\n\n- One <item>\n- Two & three\n\nFair winds,\nAlex")
    msg = decode_raw(build_raw_message(to="a@b.com", subject="s", body=body))
    assert msg.get_content_type() == "multipart/alternative"
    html = _part(msg, "html")
    assert ("<p>The ferry to the island runs twice on Sundays. I've marked the tide table, and the "
            "afternoon crossing looks calmer.</p>") in html.replace("&#x27;", "'")
    assert "<p>- One &lt;item&gt;<br>- Two &amp; three</p>" in html
    assert "<p>Fair winds,<br>Alex</p>" in html
    assert _part(msg, "plain").startswith("Hi Sam,")


def test_attachment_rides_along_with_the_body(tmp_path):
    pdf = tmp_path / "one-pager.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    raw = build_raw_message(to="a@b.com", subject="Hi", body="Hello", attachments=[str(pdf)])
    msg = email.message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)), policy=email.policy.default)
    assert msg.get_content_type() == "multipart/mixed"
    att = next(msg.iter_attachments())
    assert att.get_filename() == "one-pager.pdf"
    assert att.get_content_type() == "application/pdf"
    assert att.get_content() == b"%PDF-1.4 fake"
    assert msg.get_body(("html",)) is not None


@pytest.mark.parametrize("unit", ["<script", "<", "<style", "<a ", "</", "<script>", "</script", "&#", "\n \n"])
def test_html_to_text_stays_fast_on_hostile_mail(unit):
    started = time.perf_counter()
    html_to_text(unit * 200_000)
    assert time.perf_counter() - started < 1


def test_html_to_text_drops_scripts_and_styles_and_keeps_the_rest():
    text = html_to_text("<P>one</P><SCRIPT type=x>bad()</script ><style>.x{}</STYLE>two<br/>three &amp; four")
    assert text == "one\ntwo\nthree & four"


def test_html_to_text_hides_an_unclosed_script_as_a_browser_would():
    assert html_to_text("<p>shown</p><script>hidden") == "shown"


def test_html_to_text_keeps_a_bare_less_than():
    assert html_to_text("1 < 2") == "1 < 2"
