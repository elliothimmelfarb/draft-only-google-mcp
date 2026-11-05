# Instructions for coding agents

This repo is a Gmail and Calendar MCP server whose whole purpose is that it cannot send email. Read `README.md` first.

## The rule

Never add a code path that sends mail. That means no send tool, no Gmail send endpoint, no SMTP, no `gmail.send`, `gmail.compose` or full-mailbox scope, and no calendar option that asks Google to notify guests.

`tests/test_no_send.py` enforces this. If it fails, remove what you added. Do not edit that test to make it pass, unless you are adding a read-only or draft-only tool to its tool list.

## Layout

- `src/draft_only_google_mcp/auth.py`: OAuth login and Keychain storage. Tokens never touch disk.
- `src/draft_only_google_mcp/mail.py`: pure helpers for reading payloads and building drafts.
- `src/draft_only_google_mcp/server.py`: the MCP tools. All HTTP goes through `_api`.
- `tests/`: run with `uv run pytest`.

## Conventions

- Keep it small, with few dependencies. New settings are `DRAFT_ONLY_GOOGLE_*` environment variables with neutral defaults, and they go in the README table.
- Never commit tokens, client secret JSON files, or anyone's real email address. Test data uses made-up names.
- Only write tests that would catch a real bug.
- The server only runs on macOS because of the Keychain. Say so plainly anywhere it matters.
