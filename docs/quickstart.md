---
title: Quickstart
description: "Build the proxy fork, connect Google accounts remotely, and check Gmail and Drive as your caller."
---

# Quickstart

## 1. Build this fork

```bash
make build
bin/gog --version
```

See [Install](install.md) for the source repository and update procedure.

## 2. Connect Google accounts remotely

Open the [owner account page](https://gp-039168171bf05570eadba6a8.k-rae.com/prd/onboarding/).
The [account guide](account-onboarding.md) walks through the existing Google
project, its hosted Web client, service selection, and Google consent. Start
with `jay.jakosky@gmail.com`. Each account approves Gmail, Drive, or both.

The page saves progress remotely and separates installed access, latest attempts,
and timestamped checks. **Allow my tools to use this account** explicitly enables
the configured owner caller. Other callers need their own operator-managed grants.

`bin/gog auth add you@example.com` directs you to this page. No loopback OAuth
listener, local Google credential import, or keychain enrollment is required.

## 3. Configure the CLI

Find the configuration file with `bin/gog config path` and merge the values
from the [account guide](account-onboarding.md#use-this-fork-from-the-cli).
The proxy API key and an authorized AWS session are both required; credentials
being available does not authorize additional work.

Choose an account with `--account`, `GOG_ACCOUNT`, or `default_account` in
configuration. Local account aliases remain supported. Automatic selection works
only when remote inventory contains exactly one visible account.

## 4. Check the actual caller

```bash
bin/gog auth list --json
bin/gog --account jay.jakosky@gmail.com auth doctor --check --json
bin/gog --account jay.jakosky@gmail.com --readonly drive ls --max 1 --json
```

Ordinary inventory is metadata only. Live checks make bounded service reads
through the same signed transport your commands use. A recorded healthy server
check does not prove your caller's permission.

Gmail and Drive are the enrolled services for this deployment. Other upstream
commands require deliberate server configuration and separate verification.
Requests and responses remain subject to the proxy's transfer limits.

`--json` keeps stdout parseable; human hints go to stderr. Use `--readonly` for
reads and authorize each Google mutation explicitly.

## Where next

- [Account connections](account-onboarding.md): recovery, permissions, and disconnect.
- [Managed authentication](auth-clients.md): caller versus Google authorization.
- [Safety profiles](safety-profiles.md): local command restrictions.
- [Command index](commands/): generated command and flag reference.
