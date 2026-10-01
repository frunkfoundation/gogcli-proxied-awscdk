---
name: gog
description: "Managed gogcli proxy: remote Gmail and Drive accounts, signed requests, scoped reads and writes."
---

# gog proxy fork

Build this repository with `make build`; upstream binaries omit its proxy
transport. Google account credentials live on the server. Do not import OAuth
JSON, capture consent URLs, run loopback OAuth, or inspect local Google keyrings.

## Discover and check

```bash
gog --version
gog auth status --json --no-input
gog auth list --json --no-input
gog --account user@example.com auth doctor --check --json --no-input
GOG_HELP=agent gog --help
gog schema --json
```

Ordinary list reads signed remote metadata only. Doctor with `--check` performs
bounded reads through the normal proxy as the actual caller. Recorded server
verification and caller readiness are separate facts. A missing or locked local
Google keychain does not explain managed access failures.

The runtime needs the configured proxy endpoint, API usage-plan key, and
authorized AWS identity. Read `docs/account-onboarding.md` for config fields.
Check from the actual process environment used by the agent; credential
availability is not permission to perform work.

Select the account explicitly with `--account`. `GOG_ACCOUNT` and the configured
default are supported; automatic selection requires exactly one visible account.
Local `auth alias` shortcuts remain available.

## Connect and repair

`gog auth add`, `auth setup`, and `auth manage` display/open the hosted owner
page. The owner reviews the existing Google project, imports a hosted Web
client, selects Gmail and/or Drive, and completes Google consent there.
`auth remove` directs the owner to remote access/disconnect controls.
Never request callback URLs, OAuth codes, secret JSON, or tokens in chat.

The account page distinguishes the installed connection from the latest attempt.
A failed replacement preserves working installed access. Retry a saved check or
usable saved authorization before reconnecting. Reconnect preserves installed
services and the explicit owner-tool access setting. Removing tool access keeps
the Google connection; disconnect removes usable proxy credentials without
changing Google content or revoking shared application consent.

## Services and limits

Gmail and Drive are the enrolled services for this deployment. Other generated
service skills describe upstream commands, requiring deliberate server-side
configuration and separate verification before use. Do not treat available help
as proof of authorization.

Gmail uses `gmail.modify`; extra settings scopes and permanent deletion are not
included. Drive uses full `drive` scope, with caller/account policy restricting
operations independently. Bodies are limited to 4 MiB; serialized Lambda
responses to 6 MiB, including binary encoding overhead. Full Drive downloads and
exports use private S3 staging automatically, up to 256 MiB within the proxy request
deadline. Signed links expire after five minutes; never print them. Range requests
and other API responses retain the buffered response limit. Do not bypass the proxy.

## Safe use

- Use `--json --wrap-untrusted` for agent-readable Google content.
- Use `--readonly` for read tasks and `--no-input` for automation.
- Use `--dry-run` before supported writes; confirm exact account, object, and mutation.
- Use `--gmail-no-send` unless sending is requested. Do not add `--force` without authorization for that mutation.
- Never print AWS secrets, API keys, Google tokens, client secrets, or consent URLs.
- Proxy policies enforce authorization independently of local command guards.

```bash
gog --readonly --account user@example.com gmail search 'newer_than:3d' --max 1 --json --wrap-untrusted
gog --readonly --account user@example.com drive ls --max 1 --json --wrap-untrusted
```

For authorized Gmail body inspection, prefer `--sanitize-content` and use the
specific message command's help. Acceptance checks read only identity and at
most one message/file ID, not content. Live Google mutations require task-specific
authorization.

## Command contract

```bash
gog <service> <command> --help
gog schema <service> <command> --json
```

`--fields` aliases `--select` when the command does not define an API field mask.
`--results-only` unwraps the primary result before projection. Use item-relative
fields for lists. stdout carries data; human progress goes to stderr.

Read `docs/account-onboarding.md`, `infra/README.md`,
`docs/commands/README.md`, and `docs/safety-profiles.md` for the active contracts.
