package googleapi

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/openclaw/gogcli/internal/authclient"
	"github.com/openclaw/gogcli/internal/config"
)

func proxyFixtureContext(ctx context.Context, endpoint string) context.Context {
	deps, _ := authDependenciesFromContext(ctx)
	deps.Mode = AuthModeStored
	deps.ReadConfig = func() (config.File, error) {
		return config.File{ProxyBaseURL: endpoint, ProxyAPIKey: "fixture-key"}, nil
	}

	return WithAuthDependencies(ctx, deps)
}

func setProxyAWSCredentials(t *testing.T) {
	t.Helper()
	t.Setenv("AWS_ACCESS_KEY_ID", "fixture-access")
	t.Setenv("AWS_SECRET_ACCESS_KEY", "fixture-secret")
	t.Setenv("AWS_REGION", "us-east-1")
	t.Setenv("AWS_PROFILE", "")
	t.Setenv("GOG_AWS_PROFILE", "")
}

func TestProxyClientsRouteGeneratedAndRawRequests(t *testing.T) {
	setProxyAWSCredentials(t)
	var paths []string

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.URL.EscapedPath())
		if !allowedGoogleHost(r.Header.Get("X-GOG-Google-Host")) || r.Header.Get("X-GOG-Account") != "user@example.com" || r.Header.Get("X-Api-Key") != "fixture-key" || !strings.HasPrefix(r.Header.Get("Authorization"), "AWS4-HMAC-SHA256 ") {
			t.Errorf("proxy auth/routing headers: %v", r.Header)
		}

		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{}`))
	}))
	defer srv.Close()
	ctx := proxyFixtureContext(context.Background(), srv.URL+"/stage")

	svc, err := NewTasks(ctx, "User@Example.com")
	if err != nil {
		t.Fatal(err)
	}

	if _, err = svc.Tasklists.List().Context(ctx).Do(); err != nil {
		t.Fatal(err)
	}

	client, err := NewHTTPClientForScopes(ctx, "tasks", "User@Example.com", nil)
	if err != nil {
		t.Fatal(err)
	}
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, "https://www.googleapis.com/drive/v3/files/a%2Fb?q=x%2Fy", nil)
	req.Header.Set("X-GOG-Google-Host", "evil.test")

	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()

	if len(paths) != 2 || paths[0] != "/stage/tasks/v1/users/@me/lists" || paths[1] != "/stage/drive/v3/files/a%2Fb" {
		t.Fatalf("paths=%v", paths)
	}

	if req.URL.Host != "www.googleapis.com" || req.Header.Get("X-GOG-Google-Host") != "evil.test" {
		t.Fatal("mutated caller request")
	}

	if client.Timeout != 0 {
		t.Fatal("large downloads must have no client deadline")
	}
}

func TestProxyRawClientRejectsArbitraryHost(t *testing.T) {
	ctx := proxyFixtureContext(context.Background(), "https://abc.execute-api.us-east-1.amazonaws.com/stage")

	client, err := NewHTTPClientForScopes(ctx, "drive", "user@example.com", nil)
	if err != nil {
		t.Fatal(err)
	}
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, "https://evil.test/drive/v3/files", nil)

	resp, err := client.Do(req)
	if resp != nil {
		_ = resp.Body.Close()
	}

	if err == nil {
		t.Fatal("accepted arbitrary upstream host")
	}
}

func TestProxySigningUnknownLengthAndBodyLimit(t *testing.T) {
	req, _ := http.NewRequestWithContext(context.Background(), http.MethodPost, "https://www.googleapis.com/upload", io.MultiReader(strings.NewReader("nonempty")))
	if req.ContentLength != 0 {
		t.Fatalf("fixture length=%d", req.ContentLength)
	}
	got, err := payloadSHA256Hex(req)

	sum := sha256.Sum256([]byte("nonempty"))
	if err != nil || got != hex.EncodeToString(sum[:]) {
		t.Fatalf("hash=%q error=%v", got, err)
	}

	body, _ := io.ReadAll(req.Body)
	if string(body) != "nonempty" {
		t.Fatal("signing consumed body")
	}

	for _, known := range []bool{true, false} {
		t.Run(fmt.Sprint(known), func(t *testing.T) {
			var reader io.Reader = strings.NewReader(strings.Repeat("x", maxProxyRequestBytes+1))
			if !known {
				reader = io.MultiReader(reader)
			}

			req, _ := http.NewRequestWithContext(context.Background(), http.MethodPost, "https://www.googleapis.com/upload", reader)
			if _, err := payloadSHA256Hex(req); !errors.Is(err, errProxyRequestTooLarge) {
				t.Fatalf("error=%v", err)
			}
		})
	}
}

func TestProxyUsesScopedConfigAndRejectsConflictingAuth(t *testing.T) {
	store := config.NewConfigStore(config.Layout{ConfigDir: t.TempDir()})
	if err := store.Write(config.File{ProxyBaseURL: "https://home.execute-api.us-east-1.amazonaws.com/stage", ProxyAPIKey: "scoped-key", AWSProfile: "scoped-profile"}); err != nil {
		t.Fatal(err)
	}
	ctx := WithAuthDependencies(context.Background(), AuthDependencies{ReadConfig: store.Read})

	cfg, err := loadProxyConfig(ctx)
	if err != nil || cfg.APIKey != "scoped-key" || cfg.AWSProfile != "scoped-profile" {
		t.Fatalf("config=%#v err=%v", cfg, err)
	}

	if _, err = loadProxyConfig(WithAuthDependencies(context.Background(), AuthDependencies{})); err == nil {
		t.Fatal("missing config reader used ambient config")
	}

	for _, ctx := range []context.Context{WithAuthDependencies(ctx, AuthDependencies{Mode: AuthModeADC, ReadConfig: store.Read}), authclient.WithAccessToken(ctx, "token")} {
		if _, err := NewHTTPClientForScopes(ctx, "drive", "user@example.com", nil); err == nil {
			t.Fatal("accepted local auth mode")
		}
	}
}

type closeTrackingBody struct {
	io.Reader
	closed bool
}

func (b *closeTrackingBody) Close() error { b.closed = true; return nil }

func TestProxyTransportClosesRejectedRequestBody(t *testing.T) {
	for _, known := range []bool{true, false} {
		t.Run(fmt.Sprint(known), func(t *testing.T) {
			body := &closeTrackingBody{Reader: strings.NewReader(strings.Repeat("x", maxProxyRequestBytes+1))}

			req, _ := http.NewRequestWithContext(context.Background(), http.MethodPost, "https://www.googleapis.com/upload", body)
			if known {
				req.ContentLength = maxProxyRequestBytes + 1
			}
			base := &captureRT{}
			transport := &apiGatewayProxyTransport{Base: base}

			resp, err := transport.RoundTrip(req)
			if resp != nil {
				_ = resp.Body.Close()
			}

			if !errors.Is(err, errProxyRequestTooLarge) || !body.closed || base.last != nil {
				t.Fatalf("error=%v closed=%v sent=%v", err, body.closed, base.last != nil)
			}
		})
	}
}
