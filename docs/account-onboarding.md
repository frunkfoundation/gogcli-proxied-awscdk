# Connect your Google accounts

Open [Google account connections](https://gp-039168171bf05570eadba6a8.k-rae.com/prd/onboarding/)
and sign in with the designated owner account through Authentik. This page saves
your progress remotely. You can close it and return later.

The page separates the Google account's authorization, access for your configured
tools, and its last connection check. A previous local gogcli setup does not mean
that account is connected to this proxy.

## Use the Google project you already have

The default project is `jayj-gogcli-full-access`. Other recorded project contexts
remain available if an organization needs a different setup:

| Account with a previous local setup | Recorded project | Previous client |
| --- | --- | --- |
| `jay.jakosky@gmail.com` | `jayj-gogcli-full-access` | Desktop |
| `jay@frunkholdings.ltd` | `jay-fhl-gogcli-full-access` | Desktop |
| `treasurer@longmonttheatre.org` | `gogcli-ltc-treasurer-full` | Desktop |

One **External** Google application can connect personal Gmail and accounts in
different Workspace organizations. Each account approves its own access. An
organization may require administrator approval. You do not need a new Google
project for each email address.

Review the existing project using the page's project-specific Console links:

1. Enable the **Gmail API** and **Google Drive API** for the selected services.
   If Google shows **Manage**, that API is already enabled.
2. Review branding, **External** audience, and data access. Use production
   publishing for durable personal use. Google's Testing mode ordinarily limits
   these refresh tokens to seven days. Page confirmations record your review;
   they do not independently inspect Google's settings.
3. Add or reuse a **Web application** OAuth client in that project. The recorded
   Desktop client served local software; keep it if it is still needed.
4. Register this exact **Authorized redirect URI** on the Web client:

   ```text
   https://gp-039168171bf05570eadba6a8.k-rae.com/prd/onboarding/oauth/google/callback
   ```

5. Download the Web client's JSON and upload it on the owner page. Confirm the
   setup for that saved client revision. Replacing a client requires a new review.

This callback uses a permanent project-owned hostname. Keep the same Web client
and callback when replacing the underlying API; do not register an AWS-generated
API hostname. Each Google account still approves its own consent.

The page rejects Desktop clients and Web clients missing the exact callback.
Recorded projects and downloaded JSON do not prove API enablement, publishing,
administrator approval, or a working Google connection.

## Connect accounts

Start with `jay.jakosky@gmail.com`. Add the other addresses individually or paste
a comma/newline-separated list. Eight is the initial target; more are supported.
Choose Gmail, Drive, or both. New accounts default to both. Identity permissions
are `openid` and `email`; selected service scopes are:

| Service | Requested permission | Access, subject to proxy policy |
| --- | --- | --- |
| Gmail | `https://www.googleapis.com/auth/gmail.modify` | Read, send, label, and modify mail |
| Drive | `https://www.googleapis.com/auth/drive` | Read and manage existing Drive files |

These grants do not include every Google service or all Gmail settings commands.
The proxy's caller and account policies can narrow operations. Google permission
alone does not authorize a caller to use the proxy.

**Allow my tools to use this account** controls the configured owner caller,
identified on the page. **Connect and enable my tools** saves Google authorization
and enables that caller's account access. You can disable this setting to save
only the Google connection. Reconnect preserves the setting.

Choose the exact requested account at Google and approve the selected services.
While one account verifies, you can begin another. Secrets and refresh tokens
remain in SSM SecureString, not browser storage, CLI configuration, or Mac keychain.

## Understand status and recover

A connected account has an installed credential revision. Each service displays
its last check and timestamp. Failed reconnects are shown separately and preserve
the installed connection until a replacement passes validation.

Checks refresh saved authorization and make read-only requests: Gmail profile
and at most one message ID; Drive identity and at most one file ID. Empty
collections pass. Checks do not read message bodies or file contents, send mail,
or change Google data. The server checks its verifier identity; a CLI check
additionally proves the actual caller's access.

- If Google never returns, use the account's consent continuation/restart action.
  An expired owner session requires signing in and fresh bound consent.
- If saved authorization remains usable, retry recovery or its connection check
  rather than repeat Google consent.
- If Google reports missing permission or a wrong account, approve the intended
  account and services. Reconnect if durable authorization was revoked.
- If an API is disabled or a Workspace administrator blocks the application,
  follow the specific project/administrator instructions. Copy the client ID
  and scopes supplied by the page when administrator approval is needed.
- Refresh reloads saved state without submitting unfinished account drafts.

Temporary attempt records expire, but account controls remain available. Old
operation IDs and localhost callbacks are not needed to repair an account.
Never paste OAuth codes, secret JSON, or callback URLs containing codes into chat.

## Remove access or disconnect

Turn off **Allow my tools to use this account** to remove the configured caller's
access while retaining the Google connection. An operator must resolve broader
domain/wildcard allowlists before a per-account toggle can claim to remove access.

**Disconnect** removes usable account credentials from this proxy and stops all
proxy callers from using that account. It removes no Google content. Active
connection attempts cannot undo a disconnect. Progress remains visible until
removal and the authorization cache window have elapsed.

Authorization changes can take up to 60 seconds to affect newly starting requests.
Already authorized requests may finish within the bounded request budget. The
page waits at least 65 seconds before reporting applied removal.

Proxy disconnect does not revoke Google's consent record. For that distinct
operation, use [Google's third-party access settings](https://myaccount.google.com/connections).
Removing consent can affect other connections using the same Google account and
application grant.

## Use this fork from the CLI

Build this repository with `make build`. Upstream Homebrew/Go binaries do not
contain the proxy transport. `bin/gog --version` identifies this fork and build.
Run `bin/gog config path`, then merge these fields into its `config.json`:

```json
{
  "proxy_base_url": "https://gp-039168171bf05570eadba6a8.k-rae.com/prd/",
  "proxy_api_key": "<owner-provided usage-plan key>",
  "aws_profile": "krae-jdev-dev-prd",
  "default_account": "jay.jakosky@gmail.com"
}
```

Both an authorized AWS session and usage-plan key are required. Obtain the key
through the project operator's existing access procedure; do not configure local
Google credentials. Account diagnostics inspect remote connections:

```bash
bin/gog auth list --json
bin/gog --account jay.jakosky@gmail.com auth doctor --check --json
bin/gog --account jay.jakosky@gmail.com --readonly drive ls --max 1 --json
```

`auth add`, `auth setup`, and `auth manage` direct you to the hosted owner page.
They do not start local Google OAuth. `auth remove` directs you to the remote
access/disconnect controls. Local account aliases remain available.

Transmitted bodies are limited to 4 MiB including encoding. Responses must fit
6 MiB serialized Lambda envelopes; binary payloads have a lower ceiling because
of base64 overhead. Large uploads, downloads, and exports may exceed these bounds.
This stabilization does not add a large-file transfer path.
