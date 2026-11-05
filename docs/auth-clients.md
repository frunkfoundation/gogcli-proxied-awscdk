# Managed Google authentication

This fork stores Google client credentials and account refresh tokens on the
server. The CLI signs requests with its configured AWS identity and usage-plan
API key. The API key identifies the plan; the AWS principal identifies the caller.

Follow [Google account connections](account-onboarding.md) to import a hosted Web
client, connect accounts, choose Gmail and Drive, recover interrupted consent,
and remove access. The owner page reuses existing projects and can select another
recorded project when an organization requires it. Each operation pins the
reviewed client revision.

## CLI account commands

```bash
gog auth list --json
gog auth status --json
gog --account user@example.com auth doctor --check --json
gog auth add user@example.com
gog auth manage
gog auth remove user@example.com
```

Inventory shows only remotely enrolled accounts visible to the authenticated
caller. Status and doctor inspect the real proxy configuration and AWS credential
source. `--check` reads each selected service as that caller. Setup/add/manage
open or display the hosted owner page; remove points to its remote controls.

Select an account explicitly, through `GOG_ACCOUNT`, or with `default_account`
in configuration. If none is selected, exactly one visible remote account can
be chosen automatically. `auth alias` preserves local account shortcuts.

## Two independent permissions

Google consent authorizes selected services for the saved Google account.
Proxy access additionally requires an allowed account and permitted operations
for the caller. Caller denies cannot be overridden by account allows. Local
`--readonly` and command guards can restrict CLI use further.

Turning off owner-tool access retains the Google connection while removing that
caller's explicit account grant. Disconnect removes this proxy's usable Google
credentials. Neither action deletes Google content. Google application consent
can be revoked separately through Google's connection settings.

Do not import local OAuth JSON, export tokens, start a loopback authorization
flow, or enroll Workspace service-account keys to repair managed account access.
Those account enrollment commands are absent from this fork's managed command
tree. Unrelated Google Cloud API-key or Pub/Sub infrastructure credentials remain
scoped to their own consumers.

See [proxy infrastructure](https://github.com/Frunk-Foundation/gogcli-proxied-awscdk/blob/main/infra/README.md) for operator policies and
published integration facts.
