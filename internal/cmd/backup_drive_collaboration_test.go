package cmd

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"google.golang.org/api/drive/v3"
)

type backupCollabPageCase struct {
	name     string
	path     string
	pageSize string
	key      string
	item1    map[string]any
	item2    map[string]any
	extra    func(*testing.T, *http.Request)
	ids      func(t *testing.T, ctx context.Context, svc *drive.Service) ([]string, error)
}

func backupCollabPageCases() []backupCollabPageCase {
	return []backupCollabPageCase{
		{
			name:     "permissions",
			path:     "/files/file-1/permissions",
			pageSize: "100",
			key:      "permissions",
			item1:    map[string]any{"id": "perm-1", "type": "user", "role": "reader"},
			item2:    map[string]any{"id": "perm-2", "type": "user", "role": "writer"},
			extra:    requireSupportsAllDrives,
			ids: func(t *testing.T, ctx context.Context, svc *drive.Service) ([]string, error) {
				t.Helper()
				got, err := fetchBackupDrivePermissions(ctx, svc, "file-1")
				if err != nil {
					return nil, err
				}
				out := make([]string, len(got))
				for i, item := range got {
					out[i] = item.Id
				}
				return out, nil
			},
		},
		{
			name:     "comments",
			path:     "/files/file-1/comments",
			pageSize: "100",
			key:      "comments",
			item1:    map[string]any{"id": "comment-1", "content": "hello"},
			item2:    map[string]any{"id": "comment-2", "content": "second"},
			ids: func(t *testing.T, ctx context.Context, svc *drive.Service) ([]string, error) {
				t.Helper()
				got, err := fetchBackupDriveComments(ctx, svc, "file-1")
				if err != nil {
					return nil, err
				}
				out := make([]string, len(got))
				for i, item := range got {
					out[i] = item.Id
				}
				return out, nil
			},
		},
		{
			name:     "revisions",
			path:     "/files/file-1/revisions",
			pageSize: "200",
			key:      "revisions",
			item1:    map[string]any{"id": "rev-1", "modifiedTime": "2026-04-01T10:00:00Z"},
			item2:    map[string]any{"id": "rev-2", "modifiedTime": "2026-04-02T10:00:00Z"},
			ids: func(t *testing.T, ctx context.Context, svc *drive.Service) ([]string, error) {
				t.Helper()
				got, err := fetchBackupDriveRevisions(ctx, svc, "file-1")
				if err != nil {
					return nil, err
				}
				out := make([]string, len(got))
				for i, item := range got {
					out[i] = item.Id
				}
				return out, nil
			},
		},
	}
}

func TestFetchBackupDriveCollaborationRejectsRepeatedPageToken(t *testing.T) {
	t.Parallel()

	for _, tc := range backupCollabPageCases() {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			var calls atomic.Int32
			svc, closeSvc := newDriveTestService(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodGet || !strings.HasSuffix(r.URL.Path, tc.path) {
					http.NotFound(w, r)
					return
				}
				n := calls.Add(1)
				if n > 2 {
					http.Error(w, "unexpected extra "+tc.name+" page request", http.StatusBadRequest)
					return
				}
				wantToken := ""
				if n == 2 {
					wantToken = "stuck"
				}
				requireQuery(t, r, "pageToken", wantToken)
				requireQuery(t, r, "pageSize", tc.pageSize)
				if tc.extra != nil {
					tc.extra(t, r)
				}
				w.Header().Set("Content-Type", "application/json")
				_ = json.NewEncoder(w).Encode(map[string]any{
					tc.key:          []map[string]any{tc.item1},
					"nextPageToken": "stuck",
				})
			}))
			defer closeSvc()

			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			got, err := tc.ids(t, ctx, svc)
			if err == nil || !strings.Contains(err.Error(), "repeated page token") {
				t.Fatalf("err = %v after %d list calls", err, calls.Load())
			}
			if got != nil {
				t.Fatalf("%s = %#v, want no partial result", tc.name, got)
			}
			if n := calls.Load(); n != 2 {
				t.Fatalf("list calls = %d, want 2", n)
			}
			t.Logf("err = %v after %d list calls", err, calls.Load())
		})
	}
}

func TestFetchBackupDriveCollaborationTwoDistinctPagesSucceed(t *testing.T) {
	t.Parallel()

	for _, tc := range backupCollabPageCases() {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			var calls atomic.Int32
			svc, closeSvc := newDriveTestService(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodGet || !strings.HasSuffix(r.URL.Path, tc.path) {
					http.NotFound(w, r)
					return
				}
				n := calls.Add(1)
				if tc.extra != nil {
					tc.extra(t, r)
				}
				w.Header().Set("Content-Type", "application/json")
				if n == 1 {
					requireQuery(t, r, "pageToken", "")
					_ = json.NewEncoder(w).Encode(map[string]any{
						tc.key:          []map[string]any{tc.item1},
						"nextPageToken": "page-2",
					})
					return
				}
				if n == 2 {
					requireQuery(t, r, "pageToken", "page-2")
					_ = json.NewEncoder(w).Encode(map[string]any{
						tc.key: []map[string]any{tc.item2},
					})
					return
				}
				http.Error(w, "unexpected extra "+tc.name+" page request", http.StatusBadRequest)
			}))
			defer closeSvc()

			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			got, err := tc.ids(t, ctx, svc)
			if err != nil {
				t.Fatalf("err = %v after %d list calls", err, calls.Load())
			}
			if calls.Load() != 2 {
				t.Fatalf("list calls = %d, want 2", calls.Load())
			}
			want1, _ := tc.item1["id"].(string)
			want2, _ := tc.item2["id"].(string)
			if len(got) != 2 || got[0] != want1 || got[1] != want2 {
				t.Fatalf("%s = %#v, want both pages concatenated", tc.name, got)
			}
		})
	}
}

func TestFetchBackupDriveCollaborationRecordsRepeatedPageToken(t *testing.T) {
	t.Parallel()

	var permCalls atomic.Int32
	svc, closeSvc := newDriveTestService(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodGet && strings.HasSuffix(r.URL.Path, "/files/file-1/permissions"):
			n := permCalls.Add(1)
			if n > 2 {
				http.Error(w, "unexpected extra permission page request", http.StatusBadRequest)
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"permissions":   []map[string]any{{"id": "perm-1", "type": "user", "role": "reader"}},
				"nextPageToken": "stuck",
			})
		case r.Method == http.MethodGet && strings.HasSuffix(r.URL.Path, "/files/file-1/comments"):
			_ = json.NewEncoder(w).Encode(map[string]any{
				"comments": []map[string]any{{"id": "comment-1", "content": "hello"}},
			})
		case r.Method == http.MethodGet && strings.HasSuffix(r.URL.Path, "/files/file-1/revisions"):
			_ = json.NewEncoder(w).Encode(map[string]any{
				"revisions": []map[string]any{{"id": "rev-1", "modifiedTime": "2026-04-02T10:00:00Z"}},
			})
		default:
			http.NotFound(w, r)
		}
	}))
	defer closeSvc()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got, counts := fetchBackupDriveCollaboration(ctx, svc, []driveBackupFile{
		{File: &drive.File{Id: "file-1"}},
	})
	if permCalls.Load() != 2 {
		t.Fatalf("permission list calls = %d, want 2", permCalls.Load())
	}
	if counts["drive.collab.errors"] != 1 || counts["drive.comments"] != 1 || counts["drive.revisions"] != 1 {
		t.Fatalf("unexpected counts: %#v", counts)
	}
	if len(got.Permissions) != 1 || got.Permissions[0].FileID != "file-1" || !strings.Contains(got.Permissions[0].Error, "repeated page token") {
		t.Fatalf("permission row = %#v, want repeated page token error", got.Permissions)
	}
	if len(got.Comments) != 1 || got.Comments[0].Comment == nil || got.Comments[0].Comment.Id != "comment-1" {
		t.Fatalf("comments = %#v", got.Comments)
	}
}
