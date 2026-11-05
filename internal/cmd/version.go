package cmd

import (
	"context"
	_ "embed"
	"fmt"
	"runtime/debug"
	"strings"

	"github.com/openclaw/gogcli/internal/outfmt"
)

//go:embed VERSION
var embeddedVersion string

const sentinelDev = "dev"

var (
	version       = sentinelDev
	commit        = ""
	date          = ""
	readBuildInfo = debug.ReadBuildInfo
)

func resolvedVersion() string {
	v := strings.TrimSpace(version)
	if v != "" && v != sentinelDev {
		return v
	}
	info, ok := readBuildInfo()
	if ok {
		moduleVersion := strings.TrimSpace(info.Main.Version)
		if moduleVersion != "" && moduleVersion != "(devel)" {
			return moduleVersion
		}
	}
	if baked := strings.TrimSpace(embeddedVersion); baked != "" {
		return baked
	}
	return sentinelDev
}

func VersionString() string {
	v := "gogcli-proxy " + resolvedVersion() + " [upstream " + upstreamBaseVersion() + "]"
	sourceCommit := resolvedSourceCommit()
	if strings.TrimSpace(sourceCommit) == "" && strings.TrimSpace(date) == "" {
		return v
	}
	if strings.TrimSpace(sourceCommit) == "" {
		return fmt.Sprintf("%s (%s)", v, strings.TrimSpace(date))
	}
	if strings.TrimSpace(date) == "" {
		return fmt.Sprintf("%s (%s)", v, strings.TrimSpace(sourceCommit))
	}
	return fmt.Sprintf("%s (%s %s)", v, strings.TrimSpace(sourceCommit), strings.TrimSpace(date))
}

func upstreamBaseVersion() string { return strings.TrimSpace(embeddedVersion) }

func resolvedSourceCommit() string {
	if value := strings.TrimSpace(commit); value != "" {
		return value
	}
	if info, ok := readBuildInfo(); ok {
		for _, setting := range info.Settings {
			if setting.Key == "vcs.revision" {
				return setting.Value
			}
		}
	}
	return ""
}

type VersionCmd struct{}

func (c *VersionCmd) Run(ctx context.Context) error {
	stdout := stdoutWriter(ctx)
	if outfmt.IsJSON(ctx) {
		return outfmt.WriteJSON(ctx, stdout, map[string]any{
			"product":               "gogcli-proxy",
			"upstream_base_version": upstreamBaseVersion(),
			"version":               resolvedVersion(),
			"commit":                resolvedSourceCommit(),
			"date":                  strings.TrimSpace(date),
		})
	}
	fmt.Fprintln(stdout, VersionString())
	return nil
}
