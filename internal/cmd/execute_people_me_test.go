package cmd

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"google.golang.org/api/option"
	"google.golang.org/api/people/v1"
)

func TestExecute_PeopleMe_JSON(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !(strings.Contains(r.URL.Path, "/people/me") && r.Method == http.MethodGet) {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"resourceName": "people/me",
			"names":        []map[string]any{{"displayName": "Peter"}},
			"emailAddresses": []map[string]any{
				{"value": "a@b.com"},
			},
		})
	}))
	defer srv.Close()

	svc, err := people.NewService(context.Background(),
		option.WithoutAuthentication(),
		option.WithHTTPClient(srv.Client()),
		option.WithEndpoint(srv.URL+"/"),
	)
	if err != nil {
		t.Fatalf("NewService: %v", err)
	}
	result := executeWithPeopleContactsTestService(t, []string{"--json", "--account", "a@b.com", "people", "me"}, svc)
	if result.err != nil {
		t.Fatalf("Execute: %v", result.err)
	}

	var parsed struct {
		Person struct {
			ResourceName string `json:"resourceName"`
		} `json:"person"`
	}
	if err := json.Unmarshal([]byte(result.stdout), &parsed); err != nil {
		t.Fatalf("json parse: %v\nout=%q", err, result.stdout)
	}
	if parsed.Person.ResourceName != "people/me" {
		t.Fatalf("unexpected person: %#v", parsed.Person)
	}
}

func TestExecute_PeopleMe_Text(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !(strings.Contains(r.URL.Path, "/people/me") && r.Method == http.MethodGet) {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"resourceName": "people/me",
			"names":        []map[string]any{{"displayName": "Peter"}},
			"emailAddresses": []map[string]any{
				{"value": "a@b.com"},
			},
			"photos": []map[string]any{
				{"url": "https://example.com/p.jpg"},
			},
		})
	}))
	defer srv.Close()

	svc, err := people.NewService(context.Background(),
		option.WithoutAuthentication(),
		option.WithHTTPClient(srv.Client()),
		option.WithEndpoint(srv.URL+"/"),
	)
	if err != nil {
		t.Fatalf("NewService: %v", err)
	}
	result := executeWithPeopleContactsTestService(t, []string{"--account", "a@b.com", "people", "me"}, svc)
	if result.err != nil {
		t.Fatalf("Execute: %v", result.err)
	}
	if !strings.Contains(result.stdout, "name\tPeter") || !strings.Contains(result.stdout, "email\ta@b.com") || !strings.Contains(result.stdout, "photo\thttps://example.com/p.jpg") {
		t.Fatalf("unexpected out=%q", result.stdout)
	}
}
