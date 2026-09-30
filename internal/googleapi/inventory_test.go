package googleapi

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/openclaw/gogcli/internal/config"
)

func TestManagedInventoryRefusesRedirectAndHidesErrorBodies(t *testing.T) {
	setProxyAWSCredentials(t)
	redirected := false

	target := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { redirected = true }))
	defer target.Close()

	for _, status := range []int{http.StatusFound, http.StatusForbidden} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != "/stage/proxy/v1/accounts" || r.Header.Get("X-GOG-Account") != "" || r.Header.Get("X-GOG-Google-Host") != "" {
					t.Errorf("unexpected inventory routing: %s", r.URL.Path)
				}

				w.Header().Set("Location", target.URL)
				w.WriteHeader(status)
				fmt.Fprint(w, "sensitive error body")
			}))
			defer server.Close()

			layout, err := config.NewSystemResolver(t.TempDir()).Resolve(config.PathKindConfig)
			if err != nil {
				t.Fatal(err)
			}

			store := config.NewConfigStore(layout)
			if writeErr := store.Write(config.File{ProxyBaseURL: server.URL + "/stage/", ProxyAPIKey: "fixture-key"}); writeErr != nil {
				t.Fatal(writeErr)
			}
			ctx := WithAuthDependencies(context.Background(), AuthDependencies{ReadConfig: store.Read})

			_, err = ListManagedAccounts(ctx)
			if err == nil || !strings.Contains(err.Error(), fmt.Sprint(status)) || strings.Contains(err.Error(), "sensitive") || redirected {
				t.Fatalf("unsafe inventory failure: %v, followed redirect=%v", err, redirected)
			}
		})
	}
}
