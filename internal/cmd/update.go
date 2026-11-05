package cmd

import (
	"context"
	"fmt"
	"time"

	"github.com/openclaw/gogcli/internal/outfmt"
)

type UpdateCmd struct {
	Status UpdateStatusCmd `cmd:"" name:"status" aliases:"check" default:"withargs" help:"Show source-update instructions for this proxy fork"`
}
type UpdateStatusCmd struct {
	Timeout time.Duration `name:"timeout" hidden:"" default:"10s"`
}

func (c *UpdateStatusCmd) Run(ctx context.Context) error {
	const instructions = "Update this repository from its maintained source, review changes, and run make build. Upstream gogcli binaries do not contain this proxy transport."
	report := map[string]any{"product": "gogcli-proxy", "upstream_base_version": upstreamBaseVersion(), "version": resolvedVersion(), "source_commit": resolvedSourceCommit(), "update_method": "source", "instructions": instructions}
	if outfmt.IsJSON(ctx) {
		return outfmt.WriteJSON(ctx, stdoutWriter(ctx), report)
	}
	fmt.Fprintf(stdoutWriter(ctx), "%s\n%s\n", VersionString(), instructions)
	return nil
}
