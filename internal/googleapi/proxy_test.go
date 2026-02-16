package googleapi

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"testing"
	"time"
)

type captureRT struct {
	last *http.Request
}

func (c *captureRT) RoundTrip(r *http.Request) (*http.Response, error) {
	c.last = r
	return &http.Response{
		StatusCode: http.StatusOK,
		Body:       http.NoBody,
		Header:     make(http.Header),
		Request:    r,
	}, nil
}

func TestLoadProxyConfig_MissingEnv(t *testing.T) {
	t.Setenv(envProxyBaseURL, "")
	t.Setenv(envProxyAPIKey, "")

	_, err := loadProxyConfig()
	if err == nil {
		t.Fatalf("expected error")
	}
	var pce *ProxyConfigError
	if !errors.As(err, &pce) || pce == nil {
		t.Fatalf("expected ProxyConfigError, got: %T %v", err, err)
	}
	if !strings.Contains(err.Error(), errProxyConfig.Error()) || !strings.Contains(err.Error(), envProxyBaseURL) || !strings.Contains(err.Error(), envProxyAPIKey) {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(pce.Missing) != 2 {
		t.Fatalf("unexpected missing: %#v", pce.Missing)
	}
}

func TestInferRegionFromExecuteAPIHost(t *testing.T) {
	if got := inferRegionFromExecuteAPIHost("abc123.execute-api.us-east-1.amazonaws.com"); got != "us-east-1" {
		t.Fatalf("unexpected region: %q", got)
	}
	if got := inferRegionFromExecuteAPIHost("api.example.com"); got != "" {
		t.Fatalf("expected empty region, got %q", got)
	}
}

func TestProxyTransport_InjectsHeadersAndSigns(t *testing.T) {
	t.Setenv("AWS_ACCESS_KEY_ID", "AKID")
	t.Setenv("AWS_SECRET_ACCESS_KEY", "SECRET")
	t.Setenv("AWS_SESSION_TOKEN", "TOKEN")

	base := &captureRT{}
	tr := &apiGatewayProxyTransport{
		Base:    base,
		Region:  "us-east-1",
		APIKey:  "k",
		Account: "User@Example.com",
		Now: func() time.Time {
			return time.Date(2026, 2, 15, 12, 0, 0, 0, time.UTC)
		},
	}

	req, err := http.NewRequestWithContext(context.Background(), http.MethodGet, "https://abc123.execute-api.us-east-1.amazonaws.com/prod/gmail/v1/users/me/labels?x=1", nil)
	if err != nil {
		t.Fatalf("new request: %v", err)
	}

	resp, err := tr.RoundTrip(req)
	if err != nil {
		t.Fatalf("round trip: %v", err)
	}
	_ = resp.Body.Close()

	if base.last == nil {
		t.Fatalf("expected captured request")
	}
	r := base.last
	if got := r.Header.Get("x-api-key"); got != "k" {
		t.Fatalf("x-api-key=%q", got)
	}
	if got := r.Header.Get("X-GOG-Account"); got != "user@example.com" {
		t.Fatalf("X-GOG-Account=%q", got)
	}
	if got := r.Header.Get("X-Amz-Date"); got == "" {
		t.Fatalf("expected X-Amz-Date")
	}
	if got := r.Header.Get("X-Amz-Security-Token"); got != "TOKEN" {
		t.Fatalf("X-Amz-Security-Token=%q", got)
	}
	authz := r.Header.Get("Authorization")
	if authz == "" || !strings.Contains(authz, "AWS4-HMAC-SHA256") || !strings.Contains(authz, "execute-api") {
		t.Fatalf("unexpected Authorization=%q", authz)
	}
}
