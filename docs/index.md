---
title: Overview
permalink: /
description: "The gogcli proxy fork connects Gmail and Drive accounts remotely and enforces caller-specific access on the server."
---

## Start with your accounts

[Build this fork](install.md), then use the [hosted owner account page](account-onboarding.md)
to connect Gmail and Drive. Google credentials stay on the server. The CLI uses
an authorized AWS caller and usage-plan API key for signed requests.

```bash
bin/gog auth list --json
bin/gog --account jay.jakosky@gmail.com auth doctor --check --json
bin/gog --account jay.jakosky@gmail.com --readonly drive ls --max 1 --json
```

## What this fork provides

- Multiple remote Google accounts, with Gmail and Drive selected per account.
- Independent caller/account permissions enforced at the proxy boundary.
- Durable connection, verification, and disconnect progress on the owner page.
- Signed remote inventory and service checks as the actual CLI caller.
- Stable JSON/plain output and existing gog command safety controls.

Upstream gogcli implements additional Google commands. Their presence in help
or the generated reference does not grant this deployment access to those
services. Additional services require deliberate configuration and verification.
Bodies are limited to 4 MiB and serialized Lambda responses to 6 MiB; large-file
transfer is outside this deployment's contract.

## Documentation

- [Quickstart](quickstart.md): build, connect, configure, and check.
- [Account connections](account-onboarding.md): Google setup, recovery, and removal.
- [Managed authentication](auth-clients.md): Google authorization and caller access.
- [Infrastructure](https://github.com/Frunk-Foundation/gogcli-proxied-awscdk/blob/main/infra/README.md): exact deployment bindings and policies.
- [Safety profiles](safety-profiles.md): local restrictions in addition to proxy policy.
- [Agent skills](agent-skills.md): generated command guidance.
- [Command index](commands/): available command and flag reference.

## Attribution

This proxy fork builds on [openclaw/gogcli](https://github.com/openclaw/gogcli)
and retains its module path and MIT license. Source and changes are in the
[fork repository](https://github.com/Frunk-Foundation/gogcli-proxied-awscdk).
It is not affiliated with Google.
