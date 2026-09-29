package googleapi

import (
	"context"
	"crypto/tls"
	"errors"
	"net/http"
	"strings"
	"testing"

	"github.com/openclaw/gogcli/internal/googleauth"
)

var errBoom = errors.New("boom")

func TestOptionsForAccountScopes_HappyPath(t *testing.T) {
	opts, err := optionsForAccountScopes(proxyFixtureContext(context.Background(), "https://abc.execute-api.us-east-1.amazonaws.com/prod"), "svc", "a@b.com", []string{"s1"})
	if err != nil {
		t.Fatalf("unexpected err: %v", err)
	}

	if len(opts) == 0 {
		t.Fatalf("expected client options")
	}
}

func TestOptionsForAccount_HappyPath(t *testing.T) {
	opts, err := optionsForAccount(proxyFixtureContext(context.Background(), "https://abc.execute-api.us-east-1.amazonaws.com/prod"), googleauth.ServiceDrive, "a@b.com")
	if err != nil {
		t.Fatalf("unexpected err: %v", err)
	}

	if len(opts) == 0 {
		t.Fatalf("expected client options")
	}
}

func TestParseAuthMode(t *testing.T) {
	tests := []struct {
		value string
		want  AuthMode
	}{
		{value: "", want: AuthModeStored},
		{value: "adc", want: AuthModeADC},
		{value: "oauth", want: AuthModeStored},
		{value: "ADC", want: AuthModeStored},
		{value: " adc ", want: AuthModeStored},
	}
	for _, test := range tests {
		if got := ParseAuthMode(test.value); got != test.want {
			t.Fatalf("ParseAuthMode(%q) = %q, want %q", test.value, got, test.want)
		}
	}
}

func TestNewBaseTransport_RespectsProxyAndTLSMinimum(t *testing.T) {
	t.Setenv("HTTPS_PROXY", "http://127.0.0.1:8888")

	transport := newBaseTransport()
	if transport == nil {
		t.Fatalf("expected transport")
		return
	}

	if transport.Proxy == nil {
		t.Fatalf("expected proxy func")
	}

	if transport.TLSClientConfig == nil {
		t.Fatalf("expected TLS config")
	}

	if transport.TLSClientConfig.MinVersion < tls.VersionTLS12 {
		t.Fatalf("expected TLS min version >= 1.2, got %d", transport.TLSClientConfig.MinVersion)
	}

	req, err := http.NewRequestWithContext(context.Background(), http.MethodGet, "https://www.googleapis.com", nil)
	if err != nil {
		t.Fatalf("new request: %v", err)
	}

	proxyURL, err := transport.Proxy(req)
	if err != nil {
		t.Fatalf("proxy lookup: %v", err)
	}

	if proxyURL == nil || !strings.Contains(proxyURL.String(), "127.0.0.1:8888") {
		t.Fatalf("expected HTTPS proxy to be honored, got: %v", proxyURL)
	}
}

func TestNewBaseTransport_SetsResponseHeaderTimeout(t *testing.T) {
	transport := newBaseTransport()
	if transport.ResponseHeaderTimeout != responseHeaderTimeout {
		t.Fatalf("expected ResponseHeaderTimeout=%v, got %v", responseHeaderTimeout, transport.ResponseHeaderTimeout)
	}
}

func TestNewBoundedHTTPClient_SetsResponseHeaderTimeout(t *testing.T) {
	client := NewBoundedHTTPClient()
	if client == http.DefaultClient {
		t.Fatal("NewBoundedHTTPClient returned DefaultClient")
	}

	transport, ok := client.Transport.(*http.Transport)
	if !ok {
		t.Fatalf("expected *http.Transport, got %T", client.Transport)
	}

	if transport.ResponseHeaderTimeout != responseHeaderTimeout {
		t.Fatalf("expected ResponseHeaderTimeout=%v, got %v", responseHeaderTimeout, transport.ResponseHeaderTimeout)
	}

	if client.Timeout != 0 {
		t.Fatalf("expected no Client.Timeout, got %v", client.Timeout)
	}
}

func TestOptionsForAccountScopes_NoClientTimeout(t *testing.T) {
	opts, err := optionsForAccountScopes(proxyFixtureContext(context.Background(), "https://abc.execute-api.us-east-1.amazonaws.com/prod"), "svc", "a@b.com", []string{"s1"})
	if err != nil {
		t.Fatalf("unexpected err: %v", err)
	}

	if len(opts) == 0 {
		t.Fatalf("expected client options")
	}

	// The http.Client returned by optionsForAccountScopes must not set a
	// hard Timeout so that large file downloads (Drive videos, etc.) are
	// not interrupted. Server responsiveness is instead guarded by the
	// transport-level ResponseHeaderTimeout.
	//
	// We cannot easily extract the http.Client from option.ClientOption,
	// so we verify the transport layer instead.
	transport := newBaseTransport()
	if transport.ResponseHeaderTimeout == 0 {
		t.Fatalf("expected ResponseHeaderTimeout to be set on transport")
	}
}

func TestNewHTTPClient_NoRedirectPolicy(t *testing.T) {
	client, err := NewHTTPClient(proxyFixtureContext(context.Background(), "https://abc.execute-api.us-east-1.amazonaws.com/prod"), googleauth.ServiceDocs, "a@b.com")
	if err != nil {
		t.Fatalf("NewHTTPClient: %v", err)
	}

	if client.Transport == nil {
		t.Fatal("expected Transport to be set on client")
	}

	if client.CheckRedirect != nil {
		t.Fatal("expected no CheckRedirect on bare client")
	}
}
