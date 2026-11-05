package cmd

import (
	"context"
	"fmt"
	"io"
	"strings"

	"github.com/openclaw/gogcli/internal/googleauth"
	"github.com/openclaw/gogcli/internal/outfmt"
)

type AuthServicesCmd struct {
	Markdown bool `name:"markdown" help:"Output Markdown table"`
}

func (c *AuthServicesCmd) Run(ctx context.Context, _ *RootFlags) error {
	infos := googleauth.ServicesInfo()
	if !outfmt.IsJSON(ctx) {
		fmt.Fprintln(stderrWriter(ctx), "Upstream service support below; this deployment enrolls Gmail and Drive. Check account grants with auth list and caller reads with auth doctor --check.")
	}
	if outfmt.IsJSON(ctx) {
		return outfmt.WriteJSON(ctx, stdoutWriter(ctx), map[string]any{"services": infos, "managed_enrollment_services": []string{"gmail", "drive"}, "note": "Upstream command support and scope requirements; actual account grants and caller permission are separate. Use auth list and auth doctor --check."})
	}
	if c.Markdown {
		_, err := io.WriteString(stdoutWriter(ctx), googleauth.ServicesMarkdown(infos))
		return err
	}

	w, done := tableWriter(ctx)
	defer done()

	_, _ = fmt.Fprintln(w, "SERVICE\tUSER\tAPIS\tSCOPES\tNOTE")
	for _, info := range infos {
		_, _ = fmt.Fprintf(
			w,
			"%s\t%t\t%s\t%s\t%s\n",
			info.Service,
			info.User,
			strings.Join(info.APIs, ", "),
			strings.Join(info.Scopes, ", "),
			info.Note,
		)
	}
	return nil
}
