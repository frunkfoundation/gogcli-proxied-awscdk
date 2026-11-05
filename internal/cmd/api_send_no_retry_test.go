package cmd

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/openclaw/gogcli/internal/app"
	"github.com/openclaw/gogcli/internal/config"
)

func TestAPICallGmailSendDoesNotRetryServerError(t *testing.T) {
	for _, resource := range []string{"messages", "drafts"} {
		t.Run(resource, func(t *testing.T) {
			method := "gmail.users." + resource + ".send"
			var sends atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				switch {
				case r.Method == http.MethodGet && r.URL.Path == "/apis/gmail/v1/rest":
					_, _ = fmt.Fprintf(w, `{"rootUrl":"https://gmail.googleapis.com/","servicePath":"gmail/v1/","resources":{"users":{"resources":{%q:{"methods":{"send":{"id":%q,"httpMethod":"POST","path":"users/{userId}/%s/send","parameters":{"userId":{"location":"path","required":true}},"scopes":["https://www.googleapis.com/auth/gmail.send"]}}}}}}}`, resource, method, resource)
				case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/users/me/"+resource+"/send"):
					if sends.Add(1) == 1 {
						w.WriteHeader(http.StatusServiceUnavailable)
						_, _ = w.Write([]byte(`{"error":"send outcome unknown"}`))
					} else {
						_, _ = w.Write([]byte(`{"id":"sent-1"}`))
					}
				default:
					http.NotFound(w, r)
				}
			}))
			defer server.Close()
			t.Setenv("GOG_DISCOVERY_BASE_URL", server.URL)
			t.Setenv("GOG_PROXY_BASE_URL", server.URL)
			t.Setenv("GOG_PROXY_API_KEY", "test-key")
			t.Setenv("AWS_REGION", "us-east-1")
			t.Setenv("AWS_ACCESS_KEY_ID", "test-access-key")
			t.Setenv("AWS_SECRET_ACCESS_KEY", "test-secret-key")
			t.Setenv("AWS_SESSION_TOKEN", "")
			t.Setenv("AWS_PROFILE", "")
			t.Setenv("GOG_AWS_PROFILE", "")
			result := executeWithTestRuntime(t, []string{
				"--force", "--account", "me@example.com", "api", "call", "gmail", "v1", method,
				"--allow-write", "--no-cache", "--params", `{"userId":"me"}`, "--body", `{"raw":"bWVzc2FnZQ"}`,
			}, &app.Runtime{Config: config.NewConfigStore(config.Layout{ConfigDir: t.TempDir()})})
			if got := sends.Load(); got != 1 {
				t.Fatalf("send attempts = %d, want 1; error = %v", got, result.err)
			}
			if result.err == nil || !strings.Contains(result.err.Error(), "send outcome unknown") {
				t.Fatalf("error = %v, want original server error", result.err)
			}
		})
	}
}
