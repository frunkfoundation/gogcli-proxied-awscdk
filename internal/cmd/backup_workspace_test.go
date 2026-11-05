package cmd

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestFetchBackupFormResponsesRejectsRepeatedPageToken(t *testing.T) {
	t.Parallel()

	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || !strings.Contains(r.URL.Path, "/forms/form-1/responses") {
			http.NotFound(w, r)
			return
		}
		n := calls.Add(1)
		if n > 2 {
			http.Error(w, "unexpected extra form response page request", http.StatusBadRequest)
			return
		}
		wantToken := ""
		if n == 2 {
			wantToken = "stuck"
		}
		requireQuery(t, r, "pageToken", wantToken)
		requireQuery(t, r, "pageSize", "5000")
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"responses":     []map[string]any{{"responseId": "r1"}},
			"nextPageToken": "stuck",
		})
	}))
	defer srv.Close()

	svc := newFormsTestService(t, t.Context(), srv)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got, err := fetchBackupFormResponses(ctx, svc, "form-1")
	if err == nil || !strings.Contains(err.Error(), "repeated page token") {
		t.Fatalf("err = %v after %d list calls", err, calls.Load())
	}
	if got != nil {
		t.Fatalf("responses = %#v, want no partial result", got)
	}
	if got := calls.Load(); got != 2 {
		t.Fatalf("list calls = %d, want 2", got)
	}
	t.Logf("err = %v after %d list calls", err, calls.Load())
}

func TestFetchDriveFilesByMimeRejectsRepeatedPageToken(t *testing.T) {
	t.Parallel()

	var calls atomic.Int32
	svc, closeSvc := newDriveTestService(t, backupPagingTestHandler(t, &calls, "/files", []map[string]any{
		{"files": []map[string]any{{"id": "file-1", "name": "Survey"}}, "nextPageToken": "stuck"},
		{"files": []map[string]any{{"id": "file-1", "name": "Survey"}}, "nextPageToken": "stuck"},
	}, map[string]string{"pageSize": "1000", "q": "mimeType = 'application/vnd.google-apps.form' and trashed = false", "orderBy": "modifiedTime desc", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true", "corpora": "allDrives"}))
	defer closeSvc()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got, err := fetchDriveFilesByMime(ctx, svc, driveMimeGoogleForm)
	if err == nil || !strings.Contains(err.Error(), "repeated page token") {
		t.Fatalf("err = %v after %d list calls", err, calls.Load())
	}
	if got != nil {
		t.Fatalf("files = %#v, want no partial result", got)
	}
	if got := calls.Load(); got != 2 {
		t.Fatalf("list calls = %d, want 2", got)
	}
	t.Logf("err = %v after %d list calls", err, calls.Load())
}

func TestFetchBackupFormResponsesTwoDistinctPagesSucceed(t *testing.T) {
	t.Parallel()

	var calls atomic.Int32
	srv := httptest.NewServer(backupPagingTestHandler(t, &calls, "/forms/form-1/responses", []map[string]any{
		{"responses": []map[string]any{{"responseId": "r1"}}, "nextPageToken": "page-2"},
		{"responses": []map[string]any{{"responseId": "r2"}}},
	}, nil))
	defer srv.Close()

	svc := newFormsTestService(t, t.Context(), srv)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got, err := fetchBackupFormResponses(ctx, svc, "form-1")
	if err != nil {
		t.Fatalf("err = %v after %d list calls", err, calls.Load())
	}
	if calls.Load() != 2 {
		t.Fatalf("list calls = %d, want 2", calls.Load())
	}
	if len(got) != 2 || got[0].ResponseId != "r1" || got[1].ResponseId != "r2" {
		t.Fatalf("responses = %#v, want both pages concatenated", got)
	}
}

func TestFetchDriveFilesByMimeTwoDistinctPagesSucceed(t *testing.T) {
	t.Parallel()

	var calls atomic.Int32
	svc, closeSvc := newDriveTestService(t, backupPagingTestHandler(t, &calls, "/files", []map[string]any{
		{"files": []map[string]any{{"id": "zzz", "name": "Late"}}, "nextPageToken": "page-2"},
		{"files": []map[string]any{{"id": "aaa", "name": "Early"}}},
	}, map[string]string{"supportsAllDrives": "true", "corpora": "allDrives"}))
	defer closeSvc()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got, err := fetchDriveFilesByMime(ctx, svc, driveMimeGoogleDoc)
	if err != nil {
		t.Fatalf("err = %v after %d list calls", err, calls.Load())
	}
	if calls.Load() != 2 {
		t.Fatalf("list calls = %d, want 2", calls.Load())
	}
	if len(got) != 2 || got[0].Id != "aaa" || got[1].Id != "zzz" {
		t.Fatalf("files = %#v, want sorted ids aaa then zzz", got)
	}
}
