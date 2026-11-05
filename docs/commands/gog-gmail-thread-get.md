# `gog gmail thread get`

> Generated from `gog schema --json`. Do not edit this page by hand; run `make docs-commands`.

Get a thread with all messages (optionally download attachments)

## Usage

```bash
gog gmail (mail,email) thread (threads,read) get (info,show) <threadId> [flags]
```

## Parent

- [gog gmail thread](gog-gmail-thread.md)

## Flags

| Flag | Type | Default | Help |
| --- | --- | --- | --- |
| `--access-token` | `string` |  | Upstream direct-token option; unsupported for proxy Google API commands |
| `-a`<br>`--account`<br>`--acct` | `string` |  | Account email or alias for proxy Google API commands |
| `--client` | `string` |  | Upstream OAuth client selector; unused by managed proxy account calls |
| `--color` | `string` | auto | Color output: auto\|always\|never |
| `--disable-commands` | `string` |  | Comma-separated list of disabled commands; dot paths allowed |
| `--download` | `bool` |  | Download attachments |
| `-n`<br>`--dry-run`<br>`--dryrun`<br>`--noop`<br>`--preview` | `bool` |  | Do not make changes; print intended actions and exit successfully |
| `--enable-commands` | `string` |  | Comma-separated list of enabled command prefixes; dot paths allowed (restricts CLI) |
| `--enable-commands-exact` | `string` |  | Comma-separated list of exact enabled commands; dot paths allowed and parent commands do not enable children |
| `-y`<br>`--force`<br>`--assume-yes`<br>`--yes` | `bool` |  | Skip confirmations for destructive commands |
| `--full` | `bool` |  | Show full message bodies without truncation |
| `--gmail-no-send` | `bool` | false | Block Gmail send operations (agent safety) |
| `-h`<br>`--help` | `kong.helpFlag` |  | Show context-sensitive help. |
| `--home` | `string` |  | Override gogcli config/data/state/cache root (equivalent to GOG_HOME) |
| `-j`<br>`--json`<br>`--machine` | `bool` | false | Output JSON to stdout (best for scripting) |
| `--no-input`<br>`--non-interactive`<br>`--noninteractive` | `bool` |  | Never prompt; fail instead (useful for CI) |
| `--out-dir`<br>`--output-dir` | `string` |  | Directory to write attachments to (default: current directory) |
| `-p`<br>`--plain`<br>`--tsv` | `bool` | false | Output stable, parseable text to stdout (TSV; no colors) |
| `--quota-project` | `string` |  | Optional Google Cloud billing project (sent as X-Goog-User-Project through the proxy) |
| `--readonly` | `bool` | false | Block mutating Google API requests at runtime |
| `--results-only` | `bool` |  | In JSON mode, emit only the primary result (drops envelope fields like nextPageToken) |
| `--sanitize-content`<br>`--safe`<br>`--sanitize` | `bool` |  | Emit agent-oriented sanitized content: strip HTML, remove HTTP(S) URLs, and omit raw Gmail payloads from JSON |
| `--select`<br>`--pick`<br>`--project` | `string` |  | In JSON mode, select comma-separated fields (best-effort; supports dot paths). Desire path: use --fields for most commands. |
| `--use-indexed-attachment-ids` | `bool` |  | Use 0-based indexes as attachment ids everywhere (output, the download argument, and saved filenames) |
| `-v`<br>`--verbose` | `bool` |  | Enable verbose logging |
| `--version` | `kong.VersionFlag` |  | Print version and exit |
| `--wrap-untrusted` | `bool` | false | In JSON/raw output, wrap fetched text fields in external untrusted-content markers |

## See Also

- [gog gmail thread](gog-gmail-thread.md)
- [Command index](README.md)
