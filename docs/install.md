# Install the proxy fork

Build this repository to get the managed Google-account transport. Upstream
Homebrew packages, Docker images, and release archives install a different binary.
This fork has no maintained binary distribution yet.

```bash
git clone https://github.com/Frunk-Foundation/gogcli-proxied-awscdk.git
cd gogcli-proxied-awscdk
make build
./bin/gog --version
```

Use the Go version and toolchain declared in `go.mod`. Version output identifies
the proxy fork, upstream base, and source commit. Keep the built binary on your
PATH or invoke `bin/gog` directly.

## Connect and configure

Follow [Google account connections](account-onboarding.md) for the hosted owner
page, the existing Google projects, and proxy configuration. Google client
secrets and refresh tokens stay on the server. The local runtime needs the
proxy endpoint, usage-plan API key, and authorized AWS credentials.

```bash
bin/gog config path
bin/gog auth status --json
bin/gog auth list --json
bin/gog --account jay.jakosky@gmail.com auth doctor --check --json
```

Run these checks from the actual process environment used by your scripts or
agent. A working shell does not establish that another process has the same AWS
profile or proxy configuration. Managed account diagnostics do not need a local
Google keychain.

## Update from source

Review and pull the intended fork revision, then run `make build` again.
`gog update` describes the source-update procedure. Use this fork's source;
replacing it with an upstream binary removes the managed transport.

[Safety profiles](safety-profiles.md) can add local command restrictions. Proxy
caller/account policies independently enforce access on the server.

## Related command pages

- [`gog version`](commands/gog-version.md)
- [`gog auth status`](commands/gog-auth-status.md)
- [`gog auth add`](commands/gog-auth-add.md)
- [`gog auth doctor`](commands/gog-auth-doctor.md)
