package cmd

import (
	"context"

	"github.com/openclaw/gogcli/internal/authclient"
)

func resolveClientOverride(flags *RootFlags) string {
	if flags == nil {
		return ""
	}
	return flags.Client
}

func resolveClientForEmail(ctx context.Context, email string, flags *RootFlags) (string, error) {
	override := resolveClientOverride(flags)
	return authclient.ResolveClientWithOverride(ctx, email, override)
}
