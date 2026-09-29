package cmd

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"google.golang.org/api/gmail/v1"
	"google.golang.org/api/option"

	"github.com/openclaw/gogcli/internal/googleapi"
)

func TestGmailSendDoesNotRetryServerError(t *testing.T) {
	for _, tc := range []struct {
		name string
		args []string
	}{
		{"compose", []string{"send", "--to", "recipient@example.com", "--subject", "Hello", "--body", "Hello"}},
		{"raw", []string{"send", "--raw-file", writeRawSendEML(t)}},
		{"draft", []string{"drafts", "send", "draft-1"}},
		{"reply", []string{"reply", "msg-1", "--body", "Hello", "--no-quote"}},
		{"forward", []string{"forward", "msg-1", "--to", "recipient@example.com", "--skip-attachments"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			sends := 0
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				switch {
				case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/send"):
					sends++
					if sends == 1 {
						w.WriteHeader(http.StatusInternalServerError)
						_, _ = w.Write([]byte(`{"error":{"code":500,"message":"send outcome unknown"}}`))
					} else {
						_, _ = w.Write([]byte(`{"id":"sent-1","threadId":"thread-1"}`))
					}
				case r.Method == http.MethodGet && strings.HasSuffix(r.URL.Path, "/settings/sendAs"):
					_, _ = w.Write([]byte(`{"sendAs":[{"sendAsEmail":"me@example.com","isPrimary":true,"verificationStatus":"accepted"},{"sendAsEmail":"scoutwave@example.com","verificationStatus":"accepted"}]}`))
				case r.Method == http.MethodGet && strings.HasSuffix(r.URL.Path, "/messages/msg-1"):
					_ = json.NewEncoder(w).Encode(&gmail.Message{
						Id: "msg-1", ThreadId: "thread-1",
						Payload: &gmail.MessagePart{MimeType: "text/plain", Headers: []*gmail.MessagePartHeader{
							{Name: "From", Value: "sender@example.com"},
							{Name: "To", Value: "me@example.com"},
							{Name: "Subject", Value: "Original"},
							{Name: "Message-ID", Value: "<original@example.com>"},
						}, Body: &gmail.MessagePartBody{Data: base64.RawURLEncoding.EncodeToString([]byte("Original body"))}},
					})
				default:
					http.NotFound(w, r)
				}
			}))
			defer srv.Close()
			client := srv.Client()
			client.Transport = googleapi.NewRetryTransport(client.Transport)
			svc, err := gmail.NewService(context.Background(), option.WithHTTPClient(client), option.WithEndpoint(srv.URL+"/gmail/v1/"))
			if err != nil {
				t.Fatal(err)
			}
			args := append([]string{"--account", "me@example.com", "gmail"}, tc.args...)
			result := executeWithGmailTestService(t, args, svc)
			if sends != 1 {
				t.Fatalf("send attempts = %d, want 1; error = %v", sends, result.err)
			}
			if result.err == nil || !strings.Contains(result.err.Error(), "send outcome unknown") {
				t.Fatalf("error = %v, want original server error", result.err)
			}
		})
	}
}
