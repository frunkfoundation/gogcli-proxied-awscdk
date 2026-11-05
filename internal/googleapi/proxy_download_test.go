//nolint:wsl_v5 // Contract scenarios keep setup adjacent to assertions.
package googleapi

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
)

type downloadFixtureTransport func(*http.Request) (*http.Response, error)

func (f downloadFixtureTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestProxyDownloadHandoff(t *testing.T) {
	for _, tc := range []struct {
		name, target, payload string
		size                  int
		storageStatus         int
		fail                  bool
	}{
		{"download", "https://bucket.s3.us-east-2.amazonaws.com/key?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=secret", "PDF", 3, 200, false},
		{"truncated", "https://bucket.s3.us-east-2.amazonaws.com/key?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=secret", "P", 3, 200, true},
		{"untrusted host", "https://attacker.example/key?X-Amz-Signature=secret", "PDF", 3, 200, true},
		{"redirect", "https://bucket.s3.us-east-2.amazonaws.com/key?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=secret", "PDF", 3, 302, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			calls := 0
			transport := &apiGatewayProxyTransport{Endpoint: "https://proxy.example/prd/", Region: "us-east-2", APIKey: "api-secret", Account: "user@example.com", CredentialsProvider: &stubCredsProvider{creds: aws.Credentials{AccessKeyID: "access", SecretAccessKey: "secret"}}, Signer: &stubSigner{}}
			transport.Base = downloadFixtureTransport(func(r *http.Request) (*http.Response, error) {
				calls++
				if calls == 1 {
					if r.Header.Get("X-GOG-Response-Mode") != "signed-url" {
						t.Errorf("missing download opt-in: %v", r.Header)
					}
					envelope, _ := json.Marshal(map[string]any{"url": tc.target, "status": 200, "headers": map[string]string{"Content-Type": "application/pdf", "ETag": "original"}, "size": tc.size})
					return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": []string{"application/vnd.gog.download+json"}}, Body: io.NopCloser(strings.NewReader(string(envelope))), Request: r}, nil
				}
				if len(r.Header) != 0 || r.Method != "GET" || r.URL.String() != tc.target {
					t.Fatalf("unsafe storage request: %v", r)
				}
				return &http.Response{StatusCode: tc.storageStatus, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(tc.payload)), ContentLength: -1, Request: r}, nil
			})
			req, _ := http.NewRequestWithContext(context.Background(), "GET", "https://www.googleapis.com/drive/v3/files/file-id?alt=media", nil)
			resp, err := (&http.Client{Transport: transport}).Do(req)
			var data []byte
			if err == nil {
				defer resp.Body.Close()
				data, err = io.ReadAll(resp.Body)
			}
			if tc.fail {
				if err == nil {
					t.Fatal("expected handoff failure")
				}
				if strings.Contains(err.Error(), "secret") {
					t.Fatalf("leaked capability: %v", err)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if string(data) != "PDF" || resp.StatusCode != 200 || resp.Header.Get("Content-Type") != "application/pdf" || resp.Header.Get("ETag") != "original" || resp.ContentLength != 3 || resp.Request != req {
				t.Fatalf("incorrect restored response: %v %q", resp, data)
			}
		})
	}
}

func TestProxyDownloadEligibility(t *testing.T) {
	for _, tc := range []struct {
		method, target, byteRange string
		want                      bool
	}{
		{"GET", "https://www.googleapis.com/drive/v3/files/file-id?alt=media", "", true},
		{"GET", "https://www.googleapis.com/drive/v3/files/file-id/export?mimeType=application/pdf", "", true},
		{"GET", "https://www.googleapis.com/drive/v3/files/file-id?alt=media", "bytes=0-100", false},
		{"GET", "https://www.googleapis.com/drive/v3/files/file-id", "", false},
		{"GET", "https://www.googleapis.com/drive/v3/files?alt=media", "", false},
		{"POST", "https://www.googleapis.com/drive/v3/files/file-id?alt=media", "", false},
		{"GET", "https://gmail.googleapis.com/drive/v3/files/file-id?alt=media", "", false},
	} {
		t.Run(tc.method+tc.target+tc.byteRange, func(t *testing.T) {
			req, _ := http.NewRequestWithContext(context.Background(), tc.method, tc.target, nil)
			req.Header.Set("Range", tc.byteRange)
			clone := req.Clone(req.Context())
			clone.Header.Set("X-GOG-Response-Mode", "signed-url")
			setProxyDownloadMode(clone, req)
			if got := clone.Header.Get("X-GOG-Response-Mode") == "signed-url"; got != tc.want {
				t.Fatalf("opt-in=%v, want %v", got, tc.want)
			}
		})
	}
}
