package cmd

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestUpdateStatusSourceContract(t *testing.T) {
	for _, command := range []string{"", "status", "check"} {
		args := []string{"--json", "update"}
		if command != "" {
			args = append(args, command)
		}
		result := executeWithTestRuntime(t, args, nil)
		if result.err != nil {
			t.Fatal(result.err)
		}
		var report struct {
			Product      string `json:"product"`
			Method       string `json:"update_method"`
			Instructions string `json:"instructions"`
		}
		if err := json.Unmarshal([]byte(result.stdout), &report); err != nil {
			t.Fatal(err)
		}
		if report.Product != "gogcli-proxy" || report.Method != "source" || !strings.Contains(report.Instructions, "make build") || strings.Contains(result.stdout, "download_url") {
			t.Fatalf("incorrect source contract: %s", result.stdout)
		}
	}
}
