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

	"google.golang.org/api/cloudidentity/v1"
)

func TestFetchBackupCloudIdentityGroupsRejectsRepeatedPageToken(t *testing.T) {
	t.Parallel()

	var calls atomic.Int32
	svc := newCloudIdentityTestService(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || !strings.Contains(r.URL.Path, "groups/-/memberships:searchTransitiveGroups") {
			http.NotFound(w, r)
			return
		}
		n := calls.Add(1)
		if n > 2 {
			http.Error(w, "unexpected extra group page request", http.StatusBadRequest)
			return
		}
		wantToken := ""
		if n == 2 {
			wantToken = "stuck"
		}
		requireQuery(t, r, "pageToken", wantToken)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"memberships":   []map[string]any{{"groupKey": map[string]any{"id": "eng@example.com"}}},
			"nextPageToken": "stuck",
		})
	}))

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got, err := fetchBackupCloudIdentityGroups(ctx, svc, "admin@example.com")
	if err == nil || !strings.Contains(err.Error(), "repeated page token") {
		t.Fatalf("err = %v after %d list calls", err, calls.Load())
	}
	if got != nil {
		t.Fatalf("groups = %#v, want no partial result", got)
	}
	if got := calls.Load(); got != 2 {
		t.Fatalf("list calls = %d, want 2", got)
	}
	t.Logf("err = %v after %d list calls", err, calls.Load())
}

func TestFetchBackupCloudIdentityGroupMembersRejectsRepeatedPageToken(t *testing.T) {
	t.Parallel()

	var listCalls atomic.Int32
	svc := newCloudIdentityTestService(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.Contains(r.URL.Path, "groups:lookup"):
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{"name": "groups/abc123"})
		case r.Method == http.MethodGet && strings.Contains(r.URL.Path, "groups/abc123/memberships"):
			n := listCalls.Add(1)
			if n > 2 {
				http.Error(w, "unexpected extra membership page request", http.StatusBadRequest)
				return
			}
			wantToken := ""
			if n == 2 {
				wantToken = "stuck"
			}
			requireQuery(t, r, "pageToken", wantToken)
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{
				"memberships":   []map[string]any{{"preferredMemberKey": map[string]any{"id": "alice@example.com"}}},
				"nextPageToken": "stuck",
			})
		default:
			http.NotFound(w, r)
		}
	}))

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got := fetchBackupCloudIdentityGroupMembers(ctx, svc, []*cloudidentity.GroupRelation{
		{GroupKey: &cloudidentity.EntityKey{Id: "eng@example.com"}},
	})
	if len(got) != 1 || !strings.Contains(got[0].Error, "repeated page token") {
		t.Fatalf("members = %#v after %d list calls", got, listCalls.Load())
	}
	if got := listCalls.Load(); got != 2 {
		t.Fatalf("list calls = %d, want 2", got)
	}
	t.Logf("member error = %s after %d list calls", got[0].Error, listCalls.Load())
}

func newStuckAdminBackupHandler(t *testing.T, calls *atomic.Int32, suffix, extra, key string, item map[string]any) http.Handler {
	t.Helper()
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || !strings.HasSuffix(r.URL.Path, suffix) {
			http.NotFound(w, r)
			return
		}
		n := calls.Add(1)
		if n > 2 {
			http.Error(w, extra, http.StatusBadRequest)
			return
		}
		wantToken := ""
		if n == 2 {
			wantToken = "stuck"
		}
		requireQuery(t, r, "pageToken", wantToken)
		requireQuery(t, r, "domain", "example.com")
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			key:             []map[string]any{item},
			"nextPageToken": "stuck",
		})
	})
}

func TestFetchBackupAdminUsersRejectsRepeatedPageToken(t *testing.T) {
	t.Parallel()

	var calls atomic.Int32
	svc := newAdminTestService(t, newStuckAdminBackupHandler(t, &calls, "/users", "unexpected extra user page request", "users", map[string]any{"primaryEmail": "ada@example.com"}))

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got, err := fetchBackupAdminUsers(ctx, svc, "example.com")
	if err == nil || !strings.Contains(err.Error(), "repeated page token") {
		t.Fatalf("err = %v after %d list calls", err, calls.Load())
	}
	if got != nil {
		t.Fatalf("users = %#v, want no partial result", got)
	}
	if got := calls.Load(); got != 2 {
		t.Fatalf("list calls = %d, want 2", got)
	}
	t.Logf("err = %v after %d list calls", err, calls.Load())
}

func TestFetchBackupAdminGroupsRejectsRepeatedPageToken(t *testing.T) {
	t.Parallel()

	var calls atomic.Int32
	svc := newAdminTestService(t, newStuckAdminBackupHandler(t, &calls, "/groups", "unexpected extra admin group page request", "groups", map[string]any{"email": "eng@example.com"}))

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got, err := fetchBackupAdminGroups(ctx, svc, "example.com")
	if err == nil || !strings.Contains(err.Error(), "repeated page token") {
		t.Fatalf("err = %v after %d list calls", err, calls.Load())
	}
	if got != nil {
		t.Fatalf("groups = %#v, want no partial result", got)
	}
	if got := calls.Load(); got != 2 {
		t.Fatalf("list calls = %d, want 2", got)
	}
	t.Logf("err = %v after %d list calls", err, calls.Load())
}

func TestFetchBackupKeepNotesRejectsRepeatedPageToken(t *testing.T) {
	t.Parallel()

	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || r.URL.Path != "/v1/notes" {
			http.NotFound(w, r)
			return
		}
		n := calls.Add(1)
		if n > 2 {
			http.Error(w, "unexpected extra keep note page request", http.StatusBadRequest)
			return
		}
		wantToken := ""
		if n == 2 {
			wantToken = "stuck"
		}
		requireQuery(t, r, "pageToken", wantToken)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"notes":         []map[string]any{{"name": "notes/abc"}},
			"nextPageToken": "stuck",
		})
	}))
	t.Cleanup(srv.Close)
	svc := newKeepTestServiceFromServer(t, srv)

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got, err := fetchBackupKeepNotes(ctx, svc)
	if err == nil || !strings.Contains(err.Error(), "repeated page token") {
		t.Fatalf("err = %v after %d list calls", err, calls.Load())
	}
	if got != nil {
		t.Fatalf("notes = %#v, want no partial result", got)
	}
	if got := calls.Load(); got != 2 {
		t.Fatalf("list calls = %d, want 2", got)
	}
	t.Logf("err = %v after %d list calls", err, calls.Load())
}

func TestFetchBackupAdminUsersTwoDistinctPagesSucceed(t *testing.T) {
	t.Parallel()

	var calls atomic.Int32
	svc := newAdminTestService(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || !strings.HasSuffix(r.URL.Path, "/users") {
			http.NotFound(w, r)
			return
		}
		n := calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		if n == 1 {
			requireQuery(t, r, "pageToken", "")
			_ = json.NewEncoder(w).Encode(map[string]any{
				"users":         []map[string]any{{"primaryEmail": "zoe@example.com"}},
				"nextPageToken": "page-2",
			})
			return
		}
		if n == 2 {
			requireQuery(t, r, "pageToken", "page-2")
			_ = json.NewEncoder(w).Encode(map[string]any{
				"users": []map[string]any{{"primaryEmail": "ada@example.com"}},
			})
			return
		}
		http.Error(w, "unexpected extra user page request", http.StatusBadRequest)
	}))

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got, err := fetchBackupAdminUsers(ctx, svc, "example.com")
	if err != nil {
		t.Fatalf("err = %v after %d list calls", err, calls.Load())
	}
	if calls.Load() != 2 {
		t.Fatalf("list calls = %d, want 2", calls.Load())
	}
	if len(got) != 2 || got[0].PrimaryEmail != "ada@example.com" || got[1].PrimaryEmail != "zoe@example.com" {
		t.Fatalf("users = %#v, want sorted ada then zoe", got)
	}
}

func TestFetchBackupKeepNotesTwoDistinctPagesSucceed(t *testing.T) {
	t.Parallel()

	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || r.URL.Path != "/v1/notes" {
			http.NotFound(w, r)
			return
		}
		n := calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		if n == 1 {
			requireQuery(t, r, "pageToken", "")
			_ = json.NewEncoder(w).Encode(map[string]any{
				"notes":         []map[string]any{{"name": "notes/zzz"}},
				"nextPageToken": "page-2",
			})
			return
		}
		if n == 2 {
			requireQuery(t, r, "pageToken", "page-2")
			_ = json.NewEncoder(w).Encode(map[string]any{
				"notes": []map[string]any{{"name": "notes/aaa"}},
			})
			return
		}
		http.Error(w, "unexpected extra keep note page request", http.StatusBadRequest)
	}))
	t.Cleanup(srv.Close)
	svc := newKeepTestServiceFromServer(t, srv)

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got, err := fetchBackupKeepNotes(ctx, svc)
	if err != nil {
		t.Fatalf("err = %v after %d list calls", err, calls.Load())
	}
	if calls.Load() != 2 {
		t.Fatalf("list calls = %d, want 2", calls.Load())
	}
	if len(got) != 2 || got[0].Name != "notes/aaa" || got[1].Name != "notes/zzz" {
		t.Fatalf("notes = %#v, want sorted aaa then zzz", got)
	}
}
