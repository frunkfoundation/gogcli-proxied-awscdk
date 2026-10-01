# Connect your Google accounts

Open [Google account connections](https://gp-039168171bf05570eadba6a8.k-rae.com/prd/onboarding/)
and sign in through Authentik with the designated owner account. Saved progress
survives closing the page. The application is **gogcli-proxy**, owned by
**kraecollective.com**. The operator prepares its Google configuration separately;
see the [operator runbook](https://github.com/Frunk-Foundation/gogcli-proxied-awscdk/blob/main/infra/README.md#configure-the-google-application).

## Connect accounts

Use the owner page to manage every account. If the Google account is in another
browser profile, use a connection link as described below; that profile does
not need an owner sign-in.

For an account signed in to the owner's browser profile, choose services and press
**Connect in this browser**. Select that exact account at Google and approve the
selected permissions. Connecting enables access for
your authorized tools. If tool access was already paused, connection preserves
that pause; use **Resume tool access** when you want to restore it.

Add addresses individually or paste a comma/newline-separated list in **Add
accounts**. Eight is the current roster, not a limit. New accounts select Gmail,
Drive, and Calendar by default. You can uncheck a service before connecting.
Existing accounts keep their selected and installed services. For an existing
connection without Calendar, select Calendar and reconnect (in this browser or
through a connection link) to approve it through fresh Google consent. Installed services cannot be silently
removed through reconnect.

| Service | Access, subject to proxy policy |
| --- | --- |
| Gmail | Read, send, label, and move mail to Trash |
| Google Drive | Read, create, change, share, and delete Drive files |
| Google Calendar | Read, create, change, share, and delete calendars and events |

Google authorization and tool access are separate facts. Caller and account
policies can narrow these permissions. A previous local gogcli setup does not
establish a connection to this proxy.

## Connect an account in another browser profile

1. In the owner page, choose the services for the account and press **Create connection link**.
   For a connected account, it is under **Manage**.
2. Press **Copy link**; the button confirms with **Copied ✓**.
3. Switch to the browser profile where that Google account is signed in and paste the link into its address bar.
4. Check the account name and services, then choose **Continue to Google** and approve access.
5. The connection page shows progress and the result. The owner dashboard shows the same saved progress.

The target profile does not need an `auth.k-rae.com` login. The link permits only
this account's connection; it does not open the owner dashboard or its controls.
The link is valid for 24 hours and binds to the first profile that opens it.
Reopen in that same profile to continue. If it expires, is opened in the wrong
profile, or you clear that profile's cookies, create a new link from the
owner page. The owner page shows a link only when it is created; after a reload,
use **Create new link** if you no longer have it, which stops the old link.
Keep connection links private.

Google consent has its own 30-minute deadline after you start it. Starting
consent before the link's expiry does not shorten that approval window. A failed
or denied consent needs a fresh owner-generated link; saved credentials remain
installed until a valid replacement succeeds. Cancel an unused link in the
owner page, or replace it when you need to start again. Pausing or disconnecting
from the owner page invalidates older connection attempts.

## Understand status and recover

Connected counts include successfully checked connections to gogcli-proxy.
An account connected through the previous application needs **Reconnect**; its
working connection remains available until the replacement succeeds. A failed
reconnect does not discard installed access. Paused tool access is shown separately.

After Google approval, services are authorized but the proxy still needs to
check them. A newly installed connection waits for the access cache to expire
before those checks run. The page explains this waiting period; it counts the
account as connected only after the selected-service checks pass.

Each service shows its check status and, after a check runs, its timestamp. Checks refresh authorization
and make read-only requests: Gmail profile and at most one message ID; Drive
identity and at most one file ID; Calendar list with at most one calendar ID.
Empty collections pass. Checks read no message bodies, file contents, or event
details and change no Google data. A server check proves its named verifier
probes at that time; a CLI check additionally proves the actual caller's access.

- **Continue Google consent** resumes a valid visit bound to this owner session.
- **Restart Google consent** starts a fresh visit after expiration, cancellation,
  omitted permissions, or choosing the wrong account.
- **Retry connection** uses saved authorization when a check can be retried.
- If gogcli-proxy is not ready, contact the operator and retry after configuration.
  Existing check, pause, and disconnect controls remain available.
- If a Workspace administrator blocks the application, follow the contextual
  approval instructions for that account. Application configuration belongs to
  the operator.
- **Refresh** reloads saved progress without submitting unfinished account edits
  and shows when it last updated. The page also refreshes while work is in
  progress. An expired owner session requires signing in again.

Secrets and refresh tokens stay remotely in SSM SecureString. Never paste OAuth
codes, secret JSON, or callback URLs containing codes into chat.

## Remove access or disconnect

Use **Manage** for connection checks, reconnect, pause/resume, and
disconnect. **Pause tool access** withdraws the configured owner's account access
while retaining Google authorization. Reconnect preserves a saved pause.

**Disconnect** removes this proxy's usable credentials and blocks all proxy
callers for that account. It changes no Google content. Active or delayed
connection attempts cannot undo it. Removal remains in progress through the
writes and authorization-cache window: newly starting requests observe changes
within 60 seconds; the page waits at least 65 seconds before reporting completion.
Already authorized requests can finish within their bounded request budget.

Proxy disconnect does not revoke Google's consent record. Use
[Google's third-party access settings](https://myaccount.google.com/connections)
for that distinct action. Revocation can affect other connections using the same
Google account and application grant.

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
