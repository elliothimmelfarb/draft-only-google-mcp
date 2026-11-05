"""Pure helpers for turning Gmail API payloads into text and building drafts."""

import base64
import html
import mimetypes
import re
from pathlib import Path
from email.message import EmailMessage


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _find_part(payload: dict, mime: str) -> str | None:
    if payload.get("mimeType") == mime and payload.get("body", {}).get("data") and not payload.get("filename"):
        return _decode(payload["body"]["data"])
    for part in payload.get("parts", []) or []:
        found = _find_part(part, mime)
        if found is not None:
            return found
    return None


# Linear on purpose. Regexes over the whole body, and html.parser on Python 3.12, take
# quadratic time or worse on a body like "<script" repeated, and mail comes from strangers.
MAX_HTML_CHARS = 2_000_000
_TAG = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)")
_RAW_END = {name: re.compile(rf"</{name}\s*>", re.IGNORECASE) for name in ("script", "style")}
_LINE_ENDS = {"p", "div", "tr", "li"}


def html_to_text(s: str) -> str:
    s = s[:MAX_HTML_CHARS]
    out, i = [], 0
    while (start := s.find("<", i)) >= 0 and (end := s.find(">", start)) >= 0:
        out.append(s[i:start])
        i = end + 1
        tag = _TAG.match(s, start)
        name = tag[1].lower() if tag else ""
        closing = s[start + 1] == "/"
        if name in _RAW_END and not closing:
            close = _RAW_END[name].search(s, i)
            i = close.end() if close else len(s)
        elif name == "br" or (closing and name in _LINE_ENDS):
            out.append("\n")
    out.append(s[i:])
    text = html.unescape("".join(out))
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def extract_body(payload: dict) -> str:
    """Return the readable body of a message: text/plain if present, else stripped HTML."""
    plain = _find_part(payload, "text/plain")
    if plain is not None:
        return plain.strip()
    rich = _find_part(payload, "text/html")
    return html_to_text(rich) if rich is not None else ""


def list_attachments(payload: dict) -> list[dict]:
    out = []
    for part in payload.get("parts", []) or []:
        if part.get("filename") and part.get("body", {}).get("attachmentId"):
            out.append({
                "filename": part["filename"],
                "mimeType": part.get("mimeType"),
                "size": part["body"].get("size"),
                "attachmentId": part["body"]["attachmentId"],
            })
        out.extend(list_attachments(part))
    return out


def headers_of(payload: dict) -> dict[str, str]:
    return {h["name"]: h["value"] for h in payload.get("headers", [])}


# A line this long that runs straight into the next one was wrapped by whoever wrote it,
# not meant as a break. Mail clients keep those breaks, so the draft reads as a narrow
# column with lines ending early. Shorter lines ("Thanks,") are real breaks.
_WRAPPED = 55
_STARTS_ITEM = re.compile(r"^\s*([-*•>]|\d+[.)])\s")


def unwrap(body: str) -> str:
    """Join lines the writer wrapped by hand, keeping paragraphs, list items and quotes."""
    lines = body.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    for line in lines:
        prev = out[-1] if out else ""
        if (len(prev) >= _WRAPPED and line.strip() and not _STARTS_ITEM.match(line)
                and not prev.lstrip().startswith(">")):
            out[-1] = f"{prev.rstrip()} {line.lstrip()}"
        else:
            out.append(line)
    return "\n".join(out)


def to_html(text: str) -> str:
    """Paragraphs as <p>, single line breaks as <br>. Gmail opens a draft with an HTML part
    in its rich composer; a plain-text-only draft opens in plain-text mode, which re-wraps
    every line at about 70 characters when it sends."""
    paragraphs = re.split(r"\n\s*\n", text.strip())
    body = "".join(f"<p>{'<br>'.join(html.escape(line, quote=False) for line in p.split(chr(10)))}</p>"
                   for p in paragraphs)
    return f'<div dir="ltr">{body}</div>'


def build_raw_message(
    *,
    to: str,
    subject: str | None,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
    reply_to: dict[str, str] | None = None,
    attachments: list[str] | None = None,
) -> str:
    """Build a base64url RFC 822 message. reply_to holds the original's headers; attachments are local paths."""
    msg = EmailMessage()
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    if reply_to:
        original_subject = reply_to.get("Subject", "")
        if subject is None:
            subject = original_subject if re.match(r"(?i)^re:", original_subject) else f"Re: {original_subject}"
        mid = reply_to.get("Message-ID") or reply_to.get("Message-Id")
        if mid:
            msg["In-Reply-To"] = mid
            refs = reply_to.get("References")
            msg["References"] = f"{refs} {mid}" if refs else mid
    msg["Subject"] = subject or ""
    text = unwrap(body)
    msg.set_content(text)
    msg.add_alternative(to_html(text), subtype="html")
    for path in attachments or []:
        f = Path(path).expanduser()
        maintype, subtype = (mimetypes.guess_type(f.name)[0] or "application/octet-stream").split("/")
        msg.add_attachment(f.read_bytes(), maintype=maintype, subtype=subtype, filename=f.name)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()
