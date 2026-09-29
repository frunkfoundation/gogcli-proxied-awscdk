package cmd

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"google.golang.org/api/calendar/v3"
	"google.golang.org/api/cloudidentity/v1"
)

func TestBackupACLPagingPreservesOrdinaryErrors(t *testing.T) {
	for _, cycle := range []bool{false, true} {
		t.Run(map[bool]string{false: "provider-error", true: "cycle"}[cycle], func(t *testing.T) {
			calls := 0
			svc, cleanup := newCalendarServiceForTest(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				if strings.Contains(r.URL.Path, "/calendars/second/acl") {
					_ = json.NewEncoder(w).Encode(map[string]any{"items": []map[string]any{{"id": "second-rule"}}})
					return
				}
				calls++
				if calls > 2 || (!cycle && calls == 2) {
					http.Error(w, "permission denied", http.StatusForbidden)
					return
				}
				_ = json.NewEncoder(w).Encode(map[string]any{"items": []map[string]any{{"id": "first-rule"}}, "nextPageToken": "again"})
			}))
			defer cleanup()
			rows := fetchBackupCalendarACLRules(context.Background(), svc, []*calendar.CalendarListEntry{{Id: "first"}, {Id: "second"}})
			firstRules, secondRules, errors := 0, 0, 0
			for _, row := range rows {
				if row.Rule != nil && row.Rule.Id == "first-rule" {
					firstRules++
				}
				if row.Rule != nil && row.Rule.Id == "second-rule" {
					secondRules++
				}
				if row.Error != "" {
					errors++
					if cycle && !strings.Contains(row.Error, "repeated page token") {
						t.Fatalf("expected cycle error, got %q", row.Error)
					}
				}
			}
			wantFirst := 1
			if cycle {
				wantFirst = 0
			}
			if calls != 2 || firstRules != wantFirst || secondRules != 1 || errors != 1 {
				t.Fatalf("calls=%d first=%d second=%d errors=%d", calls, firstRules, secondRules, errors)
			}
		})
	}
}

func TestBackupMembershipPagingPreservesOrdinaryErrors(t *testing.T) {
	calls := 0
	svc := newCloudIdentityTestService(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if strings.Contains(r.URL.Path, "groups:lookup") {
			name := "groups/first"
			if r.URL.Query().Get("groupKey.id") == "second@example.com" {
				name = "groups/second"
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"name": name})
			return
		}
		if strings.Contains(r.URL.Path, "groups/second/memberships") {
			_ = json.NewEncoder(w).Encode(map[string]any{"memberships": []map[string]any{{"name": "second-member"}}})
			return
		}
		calls++
		if calls > 1 {
			http.Error(w, "permission denied", http.StatusForbidden)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"memberships": []map[string]any{{"name": "first-member"}}, "nextPageToken": "next"})
	}))
	rows := fetchBackupCloudIdentityGroupMembers(context.Background(), svc, []*cloudidentity.GroupRelation{
		{GroupKey: &cloudidentity.EntityKey{Id: "first@example.com"}},
		{GroupKey: &cloudidentity.EntityKey{Id: "second@example.com"}},
	})
	first, second, errors := false, false, 0
	for _, row := range rows {
		if row.Member != nil {
			first = first || row.Member.Name == "first-member"
			second = second || row.Member.Name == "second-member"
		}
		if row.Error != "" {
			errors++
		}
	}
	if calls != 2 || !first || !second || errors != 1 {
		t.Fatalf("calls=%d first=%v second=%v errors=%d", calls, first, second, errors)
	}
}

func TestRecurringInstancePagingStopsAtMatch(t *testing.T) {
	calls := 0
	svc, cleanup := newCalendarServiceForTest(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if calls > 1 {
			http.Error(w, "must not fetch after matching", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"items":         []map[string]any{{"id": "found", "originalStartTime": map[string]any{"dateTime": "2026-09-05T10:00:00Z"}}},
			"nextPageToken": "unnecessary",
		})
	}))
	defer cleanup()
	id, err := resolveRecurringInstanceID(context.Background(), svc, "cal", "series", "2026-09-05T10:00:00Z")
	if err != nil || id != "found" || calls != 1 {
		t.Fatalf("id=%q calls=%d err=%v", id, calls, err)
	}
}

func TestContactsDedupePagingStopsAtLimit(t *testing.T) {
	calls := 0
	svc, cleanup := newPeopleService(t, func(w http.ResponseWriter, r *http.Request) {
		calls++
		if calls > 1 {
			http.Error(w, "must not fetch after limit", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"connections":   []map[string]any{{"resourceName": "people/one"}},
			"nextPageToken": "unnecessary",
		})
	})
	defer cleanup()
	rows, err := contactsDedupeList(context.Background(), svc, 1)
	if err != nil || len(rows) != 1 || calls != 1 {
		t.Fatalf("rows=%v calls=%d err=%v", rows, calls, err)
	}
}
