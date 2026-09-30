# `gog youtube playlists delete`

> Generated from `gog schema --json`. Do not edit this page by hand; run `make docs-commands`.

Delete a playlist

## Usage

```bash
gog youtube (yt) playlists (playlist) delete (del) <playlist-id>
```

## Parent

- [gog youtube playlists](gog-youtube-playlists.md)

## Flags

| Flag | Type | Default | Help |
| --- | --- | --- | --- |
| `--access-token` | `string` |  | Upstream direct-token option; unsupported for proxy Google API commands |
| `-a`<br>`--account`<br>`--acct` | `string` |  | Account email or alias for proxy Google API commands |
| `--client` | `string` |  | Upstream OAuth client selector; unused by managed proxy account calls |
| `--color` | `string` | auto | Color output: auto\|always\|never |
| `--disable-commands` | `string` |  | Comma-separated list of disabled commands; dot paths allowed |
| `-n`<br>`--dry-run`<br>`--dryrun`<br>`--noop`<br>`--preview` | `bool` |  | Do not make changes; print intended actions and exit successfully |
| `--enable-commands` | `string` |  | Comma-separated list of enabled command prefixes; dot paths allowed (restricts CLI) |
| `--enable-commands-exact` | `string` |  | Comma-separated list of exact enabled commands; dot paths allowed and parent commands do not enable children |
| `-y`<br>`--force`<br>`--assume-yes`<br>`--yes` | `bool` |  | Skip confirmations for destructive commands |
| `--gmail-no-send` | `bool` | false | Block Gmail send operations (agent safety) |
| `-h`<br>`--help` | `kong.helpFlag` |  | Show context-sensitive help. |
| `--home` | `string` |  | Override gogcli config/data/state/cache root (equivalent to GOG_HOME) |
| `-j`<br>`--json`<br>`--machine` | `bool` | false | Output JSON to stdout (best for scripting) |
| `--no-input`<br>`--non-interactive`<br>`--noninteractive` | `bool` |  | Never prompt; fail instead (useful for CI) |
| `-p`<br>`--plain`<br>`--tsv` | `bool` | false | Output stable, parseable text to stdout (TSV; no colors) |
| `--quota-project` | `string` |  | Optional Google Cloud billing project (sent as X-Goog-User-Project through the proxy) |
| `--readonly` | `bool` | false | Block mutating Google API requests at runtime |
| `--results-only` | `bool` |  | In JSON mode, emit only the primary result (drops envelope fields like nextPageToken) |
| `--select`<br>`--pick`<br>`--project` | `string` |  | In JSON mode, select comma-separated fields (best-effort; supports dot paths). Desire path: use --fields for most commands. |
| `-v`<br>`--verbose` | `bool` |  | Enable verbose logging |
| `--version` | `kong.VersionFlag` |  | Print version and exit |
| `--wrap-untrusted` | `bool` | false | In JSON/raw output, wrap fetched text fields in external untrusted-content markers |

## See Also

- [gog youtube playlists](gog-youtube-playlists.md)
- [Command index](README.md)
