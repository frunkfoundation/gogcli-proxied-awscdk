# Remote Gmail account setup

Open [Gmail access](https://u5q3kdnam4.execute-api.us-east-2.amazonaws.com/prd/onboarding/)
and sign in through the existing Authentik owner login. This canonical HTTPS page
is recorded in `env/gogcli-prd/access.json`; this
workflow does not start a local server. Only the designated owner can upload a
Google client, edit the checklist, or connect accounts.

The numbered page guides you through one Google Cloud project:

1. Create or select a project and enter its Project ID to scope the Console links.
2. Enable the Gmail API.
3. Configure External Audience, Branding, and Data Access. The requested scopes
   are `openid`, `email`, and `https://www.googleapis.com/auth/gmail.modify`.
   Publish the app **before** connecting accounts. External apps in Testing issue
   Gmail refresh tokens that expire after seven days. The publishing checkbox
   records your confirmation; the page cannot verify Google's publishing status.
4. Create a **Web application** OAuth client. Copy the exact callback displayed
   by the page into Authorized redirect URIs, save, and download its JSON. Upload
   that JSON on the page. A Desktop client or JSON missing that exact callback is
   rejected before any Google redirect.
5. Connect each mailbox separately, beginning with `jay.jakosky@gmail.com`.
   `jay@frunkholdings.ltd` and `treasurer@longmonttheatre.org` are prefilled, with
   five additional slots. Add more slots as needed. Google consent opens in the
   same tab; select the exact mailbox requested and approve access.

The page saves checklist entries, the project, and publishing confirmation on
the server. It does not store them in localStorage. Credentials and refresh tokens
remain in remote SSM SecureString storage, never in browser storage or the Mac
keychain. Each mailbox's persisted operation survives closing the page. Reopen
it to resume; busy operations refresh automatically.

“Verified” appears only after a real signed proxy read checks the Gmail profile
and a listing of at most one message ID after at least 65 seconds, outlasting the
proxy credential cache. An empty mailbox passes. This check sends and modifies
no mail. A saved authorization is distinct from a successful proxy check.

If a proxy check fails, read the row's recovery message, resolve the reported
access or service issue, and choose **Retry proxy check**. That retry uses the
saved authorization without another Google consent. An incomplete Google
consent can be continued or retried separately. **Refresh status** reloads current
operation results; **Reload saved progress** also works if the first load failed.
If the owner session expires, sign in again to resume.

## First CLI read

Build this fork with `make build`. Upstream Homebrew and Go installs do not
include its proxy transport. The page shows the deployed proxy URL and the
first-caller AWS profile `krae-jdev-dev-prd` after an account is verified.

Run `bin/gog config path`, then merge the following into the printed `config.json`:

```json
{
  "proxy_base_url": "https://<api-id>.execute-api.us-east-2.amazonaws.com/prd/",
  "proxy_api_key": "<owner-provided proxy API key>",
  "aws_profile": "krae-jdev-dev-prd",
  "default_account": "jay.jakosky@gmail.com"
}
```

Both the usage-plan API key and an authorized AWS session are required. Do not
put Google client JSON or Google tokens in the CLI configuration. Run:

```bash
bin/gog --account jay.jakosky@gmail.com --readonly gmail search 'newer_than:7d' --max 1 --json
```

An empty result is successful. Page verification checks the server path; this
command additionally proves your actual CLI caller's credentials and access.
Change `--account` to use another verified mailbox. Local `gog auth` commands do
not register credentials with this remote account manager.
