package cmd

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestFetchBackupSharedDrivesRejectsRepeatedPageToken(t *testing.T) {
	t.Parallel()

	var calls atomic.Int32
	svc, closeSvc := newDriveTestService(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || !strings.HasSuffix(r.URL.Path, "/drives") {
			http.NotFound(w, r)
			return
		}
		n := calls.Add(1)
		if n > 2 {
			http.Error(w, "unexpected extra shared drive page request", http.StatusBadRequest)
			return
		}
		wantToken := ""
		if n == 2 {
			wantToken = "stuck"
		}
		requireQuery(t, r, "pageToken", wantToken)
		requireQuery(t, r, "pageSize", "100")
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"drives":        []map[string]any{{"id": "drive-1", "name": "Team"}},
			"nextPageToken": "stuck",
		})
	}))
	defer closeSvc()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got, err := fetchBackupSharedDrives(ctx, svc)
	if err == nil || !strings.Contains(err.Error(), "repeated page token") {
		t.Fatalf("err = %v after %d list calls", err, calls.Load())
	}
	if got != nil {
		t.Fatalf("drives = %#v, want no partial result", got)
	}
	if got := calls.Load(); got != 2 {
		t.Fatalf("list calls = %d, want 2", got)
	}
	t.Logf("err = %v after %d list calls", err, calls.Load())
}

func TestFetchBackupDriveFilesRejectsRepeatedPageToken(t *testing.T) {
	t.Parallel()

	var calls atomic.Int32
	svc, closeSvc := newDriveTestService(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || !strings.HasSuffix(r.URL.Path, "/files") {
			http.NotFound(w, r)
			return
		}
		n := calls.Add(1)
		if n > 2 {
			http.Error(w, "unexpected extra drive file page request", http.StatusBadRequest)
			return
		}
		wantToken := ""
		if n == 2 {
			wantToken = "stuck"
		}
		requireQuery(t, r, "pageToken", wantToken)
		requireQuery(t, r, "pageSize", "1000")
		requireQuery(t, r, "q", "trashed = false")
		requireQuery(t, r, "orderBy", "modifiedTime desc")
		requireSupportsAllDrives(t, r)
		requireQuery(t, r, "includeItemsFromAllDrives", "true")
		requireQuery(t, r, "corpora", "allDrives")
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"files":         []map[string]any{{"id": "file-1", "name": "notes.txt"}},
			"nextPageToken": "stuck",
		})
	}))
	defer closeSvc()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got, err := fetchBackupDriveFiles(ctx, svc)
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

func TestFetchBackupSharedDrivesTwoDistinctPagesSucceed(t *testing.T) {
	t.Parallel()

	var calls atomic.Int32
	svc, closeSvc := newDriveTestService(t, backupPagingTestHandler(t, &calls, "/drives", []map[string]any{
		{"drives": []map[string]any{{"id": "zzz", "name": "Late"}}, "nextPageToken": "page-2"},
		{"drives": []map[string]any{{"id": "aaa", "name": "Early"}}},
	}, nil))
	defer closeSvc()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got, err := fetchBackupSharedDrives(ctx, svc)
	if err != nil {
		t.Fatalf("err = %v after %d list calls", err, calls.Load())
	}
	if calls.Load() != 2 {
		t.Fatalf("list calls = %d, want 2", calls.Load())
	}
	if len(got) != 2 || got[0].Id != "aaa" || got[1].Id != "zzz" {
		t.Fatalf("drives = %#v, want sorted ids aaa then zzz", got)
	}
}

func TestFetchBackupDriveFilesTwoDistinctPagesSucceed(t *testing.T) {
	t.Parallel()

	var calls atomic.Int32
	svc, closeSvc := newDriveTestService(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || !strings.HasSuffix(r.URL.Path, "/files") {
			http.NotFound(w, r)
			return
		}
		n := calls.Add(1)
		requireSupportsAllDrives(t, r)
		requireQuery(t, r, "corpora", "allDrives")
		w.Header().Set("Content-Type", "application/json")
		if n == 1 {
			requireQuery(t, r, "pageToken", "")
			_ = json.NewEncoder(w).Encode(map[string]any{
				"files":         []map[string]any{{"id": "file-1", "name": "a.txt"}},
				"nextPageToken": "page-2",
			})
			return
		}
		if n == 2 {
			requireQuery(t, r, "pageToken", "page-2")
			_ = json.NewEncoder(w).Encode(map[string]any{
				"files": []map[string]any{{"id": "file-2", "name": "b.txt"}},
			})
			return
		}
		http.Error(w, "unexpected extra drive file page request", http.StatusBadRequest)
	}))
	defer closeSvc()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got, err := fetchBackupDriveFiles(ctx, svc)
	if err != nil {
		t.Fatalf("err = %v after %d list calls", err, calls.Load())
	}
	if calls.Load() != 2 {
		t.Fatalf("list calls = %d, want 2", calls.Load())
	}
	if len(got) != 2 || got[0].File.Id != "file-1" || got[1].File.Id != "file-2" {
		t.Fatalf("files = %#v, want both pages concatenated", got)
	}
}
