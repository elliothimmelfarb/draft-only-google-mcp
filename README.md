# draft-only-google-mcp

This is a small MCP server that gives Claude access to one Gmail mailbox and its Google Calendar. Claude can read mail, manage labels, write drafts and work with calendar events. It has no way to send email, so you review every draft and send it yourself from Gmail.

It is about 460 lines of Python. It depends only on Anthropic's `mcp` SDK, Google's auth libraries and `requests`, and it talks only to the Gmail API, the Calendar API and Google OAuth.

**macOS only.** Tokens are stored in the macOS Keychain and nowhere else, and the server refuses to start a login or read a token on any other system. Linux and Windows are not supported today.

## What the guarantee covers

There is no code path in this server that sends mail. It has no send tool, it never calls Gmail's send endpoints, and it does not speak SMTP. Calendar writes always pass `sendUpdates=none`, and no tool has a parameter that changes that.

`tests/test_no_send.py` enforces this. It fails if the source mentions a send endpoint, a send scope or SMTP, if the OAuth scopes or the tool list change from a fixed list, if a tool gains a parameter with `send` or `notify` in its name, or if a calendar write asks Google to notify guests.

## What the guarantee does not cover

The guarantee is about this server's code, not about the Google token, so these limits matter:

- **The token itself can send.** Gmail has no scope that allows drafts without also allowing send, so this server uses `gmail.modify`, which Google describes as read, compose and send. Other software holding this token could send mail.
- **An agent with a shell can reach the token.** The token is stored in the Keychain by the `security` command, so a process running as your user can read it back with that same command without a prompt. If you need the limit to hold, do not also give the agent unrestricted shell access.
- **Calendar guests can still hear about events.** An event with attendees can appear on their calendars even though no invite is requested, and Google's documentation says some emails might still be sent. Treat adding a guest as a visible action.
- **Drafts can hold private content.** `create_draft` can attach any local file the server can read, and mail you ask Claude to read can contain instructions written by strangers. Read each draft and its recipients before you send it.

## Tools

- Gmail: `search_threads`, `get_thread`, `get_message`, `download_attachment`, `list_labels`, `modify_thread_labels`, `create_draft`, `update_draft`, `list_drafts`
- Calendar: `list_calendars`, `list_events`, `get_event`, `create_event`, `update_event`

## Setup

You need macOS, [uv](https://docs.astral.sh/uv/) and a Google account. You create your own Google OAuth client, so no third party is involved and your token only works with your client.

### 1. Create a Google OAuth client

1. Open the [Google Cloud console](https://console.cloud.google.com/) and create a new project.
2. Under APIs & Services, open Library, then enable the Gmail API and the Google Calendar API.
3. Open Google Auth Platform and fill in the app name and your email address when asked. Choose the audience:
   - With a Google Workspace account, choose Internal. Only people in your organization can use the app, and no review is needed.
   - With a personal Gmail account, choose External, then add your own address as a test user under Audience.
4. Under Clients, create an OAuth client with the application type Desktop app, then download its JSON file.

An External app in Testing status gets a refresh token that expires after 7 days, so you would need to log in again each week. To avoid that, publish the app from the Audience page. Google will then show an unverified app warning at login, which you can click through because it is your own app.

### 2. Sign in

```sh
git clone https://github.com/elliothimmelfarb/draft-only-google-mcp.git
cd draft-only-google-mcp
uv run draft-only-google-mcp login /path/to/client_secret.json
```

A browser opens for Google's consent screen. When you are signed in, the token is in the Keychain and you should delete the JSON file. Run `uv run draft-only-google-mcp whoami` to check which account is stored.

### 3. Add it to Claude Code

```sh
claude mcp add -s user draft-only-google -- uv run --directory "$PWD" draft-only-google-mcp
```

## Configuration

All settings are optional environment variables. Set them for both the `login` command and the server, for example with `claude mcp add -e NAME=value`.

| Variable | Default | Purpose |
|---|---|---|
| `DRAFT_ONLY_GOOGLE_ACCOUNT` | none | Makes `login` refuse any other Google account. Also the Keychain account name; without it the name is `default`. |
| `DRAFT_ONLY_GOOGLE_KEYCHAIN_SERVICE` | `draft-only-google-mcp` | The Keychain service name. |
| `DRAFT_ONLY_GOOGLE_SERVER_NAME` | `draft-only-google` | The name the MCP server reports. |
| `DRAFT_ONLY_GOOGLE_ATTACHMENT_DIR` | `~/Downloads/draft-only-google-mcp` | Where `download_attachment` saves files. |
| `DRAFT_ONLY_GOOGLE_TIME_ZONE` | `UTC` | The default time zone for calendar events. |

To use two mailboxes, register the server twice with a different `DRAFT_ONLY_GOOGLE_ACCOUNT` for each and run `login` once for each.

## Development

```sh
uv run pytest
```

## License

MIT. See [LICENSE](LICENSE).
