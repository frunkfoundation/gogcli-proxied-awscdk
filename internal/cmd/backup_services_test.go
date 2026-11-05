package cmd

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"google.golang.org/api/calendar/v3"
	"google.golang.org/api/people/v1"
	"google.golang.org/api/tasks/v1"
)

func TestFetchBackupCalendarsRejectsRepeatedPageToken(t *testing.T) {
	var calls atomic.Int32
	svc, closeSvc := newCalendarServiceForTest(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || !strings.Contains(r.URL.Path, "/calendarList") {
			http.NotFound(w, r)
			return
		}
		n := calls.Add(1)
		if n > 2 {
			http.Error(w, "unexpected extra calendar page request", http.StatusBadRequest)
			return
		}
		wantToken := ""
		if n == 2 {
			wantToken = "stuck"
		}
		requireQuery(t, r, "pageToken", wantToken)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"items":         []map[string]any{{"id": "cal-1", "summary": "Work"}},
			"nextPageToken": "stuck",
		})
	}))
	defer closeSvc()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got, err := fetchBackupCalendars(ctx, svc)
	if err == nil || !strings.Contains(err.Error(), "repeated page token") {
		t.Fatalf("err = %v after %d list calls", err, calls.Load())
	}
	if got != nil {
		t.Fatalf("calendars = %#v, want no partial result", got)
	}
	if got := calls.Load(); got != 2 {
		t.Fatalf("list calls = %d, want 2", got)
	}
	t.Logf("err = %v after %d list calls", err, calls.Load())
}

func TestFetchBackupConnectionsRejectsRepeatedPageToken(t *testing.T) {
	var calls atomic.Int32
	svc, closeSvc := newGoogleTestService(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || !strings.Contains(r.URL.Path, "people/me/connections") {
			http.NotFound(w, r)
			return
		}
		n := calls.Add(1)
		if n > 2 {
			http.Error(w, "unexpected extra connections page request", http.StatusBadRequest)
			return
		}
		wantToken := ""
		if n == 2 {
			wantToken = "stuck"
		}
		requireQuery(t, r, "pageToken", wantToken)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"connections":   []map[string]any{{"resourceName": "people/c1"}},
			"nextPageToken": "stuck",
		})
	}), people.NewService)
	defer closeSvc()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got, err := fetchBackupConnections(ctx, svc)
	if err == nil || !strings.Contains(err.Error(), "repeated page token") {
		t.Fatalf("err = %v after %d list calls", err, calls.Load())
	}
	if got != nil {
		t.Fatalf("connections = %#v, want no partial result", got)
	}
	if got := calls.Load(); got != 2 {
		t.Fatalf("list calls = %d, want 2", got)
	}
	t.Logf("err = %v after %d list calls", err, calls.Load())
}

func TestFetchBackupTaskListsRejectsRepeatedPageToken(t *testing.T) {
	var calls atomic.Int32
	svc, closeSvc := newGoogleTestService(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || !strings.HasSuffix(r.URL.Path, "/users/@me/lists") {
			http.NotFound(w, r)
			return
		}
		n := calls.Add(1)
		if n > 2 {
			http.Error(w, "unexpected extra task list page request", http.StatusBadRequest)
			return
		}
		wantToken := ""
		if n == 2 {
			wantToken = "stuck"
		}
		requireQuery(t, r, "pageToken", wantToken)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"items":         []map[string]any{{"id": "list-1", "title": "Inbox"}},
			"nextPageToken": "stuck",
		})
	}), tasks.NewService)
	defer closeSvc()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got, err := fetchBackupTaskLists(ctx, svc)
	if err == nil || !strings.Contains(err.Error(), "repeated page token") {
		t.Fatalf("err = %v after %d list calls", err, calls.Load())
	}
	if got != nil {
		t.Fatalf("task lists = %#v, want no partial result", got)
	}
	if got := calls.Load(); got != 2 {
		t.Fatalf("list calls = %d, want 2", got)
	}
	t.Logf("err = %v after %d list calls", err, calls.Load())
}

func TestFetchBackupCalendarsTwoDistinctPagesSucceed(t *testing.T) {
	var calls atomic.Int32
	svc, closeSvc := newCalendarServiceForTest(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || !strings.Contains(r.URL.Path, "/calendarList") {
			http.NotFound(w, r)
			return
		}
		n := calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		if n == 1 {
			requireQuery(t, r, "pageToken", "")
			_ = json.NewEncoder(w).Encode(map[string]any{
				"items":         []map[string]any{{"id": "zzz", "summary": "Late"}},
				"nextPageToken": "page-2",
			})
			return
		}
		if n == 2 {
			requireQuery(t, r, "pageToken", "page-2")
			_ = json.NewEncoder(w).Encode(map[string]any{
				"items": []map[string]any{{"id": "aaa", "summary": "Early"}},
			})
			return
		}
		http.Error(w, "unexpected extra calendar page request", http.StatusBadRequest)
	}))
	defer closeSvc()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got, err := fetchBackupCalendars(ctx, svc)
	if err != nil {
		t.Fatalf("err = %v after %d list calls", err, calls.Load())
	}
	if calls.Load() != 2 {
		t.Fatalf("list calls = %d, want 2", calls.Load())
	}
	if len(got) != 2 || got[0].Id != "aaa" || got[1].Id != "zzz" {
		t.Fatalf("calendars = %#v, want sorted ids aaa then zzz", got)
	}
}

func TestFetchBackupCalendarEventsRejectsRepeatedPageToken(t *testing.T) {
	var calls atomic.Int32
	svc, closeSvc := newCalendarServiceForTest(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || !strings.Contains(r.URL.Path, "/calendars/cal-1/events") {
			http.NotFound(w, r)
			return
		}
		n := calls.Add(1)
		if n > 2 {
			http.Error(w, "unexpected extra event page request", http.StatusBadRequest)
			return
		}
		wantToken := ""
		if n == 2 {
			wantToken = "stuck"
		}
		requireQuery(t, r, "pageToken", wantToken)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"items":         []map[string]any{{"id": "evt-1", "summary": "Standup"}},
			"nextPageToken": "stuck",
		})
	}))
	defer closeSvc()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got, err := fetchBackupCalendarEvents(ctx, svc, []*calendar.CalendarListEntry{{Id: "cal-1"}})
	if err == nil || !strings.Contains(err.Error(), "repeated page token") {
		t.Fatalf("err = %v after %d list calls", err, calls.Load())
	}
	if got != nil {
		t.Fatalf("events = %#v, want no partial result", got)
	}
	if got := calls.Load(); got != 2 {
		t.Fatalf("list calls = %d, want 2", got)
	}
	t.Logf("err = %v after %d list calls", err, calls.Load())
}
