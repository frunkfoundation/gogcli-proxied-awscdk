package cmd

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"google.golang.org/api/drive/v3"

	"github.com/openclaw/gogcli/internal/secrets"

	"github.com/openclaw/gogcli/internal/app"
)

func managedProxyFixture(t *testing.T, inventory string, handle func(http.ResponseWriter, *http.Request)) string {
	t.Helper()
	t.Setenv("AWS_ACCESS_KEY_ID", "fixture-access")
	t.Setenv("AWS_SECRET_ACCESS_KEY", "fixture-secret")
	t.Setenv("AWS_REGION", "us-east-2")
	t.Setenv("AWS_PROFILE", "")
	t.Setenv("GOG_AWS_PROFILE", "")
	t.Setenv("GOG_ACCOUNT", "")
	t.Setenv("GOG_ACCESS_TOKEN", "")
	t.Setenv("GOG_AUTH_MODE", "")
	t.Setenv("GOG_PROXY_BASE_URL", "")
	t.Setenv("GOG_PROXY_API_KEY", "")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.Header.Get("Authorization"), "AWS4-HMAC-SHA256 ") || r.Header.Get("X-Api-Key") != "fixture-key" {
			t.Errorf("request is not signed with configured key")
		}
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/stage/proxy/v1/accounts" {
			if r.Header.Get("X-GOG-Account") != "" || r.Header.Get("X-GOG-Google-Host") != "" {
				t.Errorf("inventory sent Google routing headers")
			}
			fmt.Fprint(w, inventory)
			return
		}
		if handle == nil {
			t.Errorf("unexpected request %s", r.URL.Path)
			w.WriteHeader(404)
			return
		}
		handle(w, r)
	}))
	t.Cleanup(srv.Close)
	home := t.TempDir()
	if err := os.Mkdir(filepath.Join(home, "config"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(home, "config", "config.json"), []byte(fmt.Sprintf(`{"proxy_base_url":%q,"proxy_api_key":"fixture-key"}`, srv.URL+"/stage/")), 0o600); err != nil {
		t.Fatal(err)
	}
	return home
}

func TestManagedAuthListUsesSignedRemoteInventory(t *testing.T) {
	home := managedProxyFixture(t, `{"accounts":[{"email":"remote@example.com","connection_status":"connected","services":[{"name":"drive","authorization_status":"granted","last_verification_status":"succeeded","last_verified_at":123}]}],"setup_url":"https://example.com/stage/onboarding/"}`, nil)
	result := executeWithTestRuntime(t, []string{"--home", home, "--json", "auth", "list"}, &app.Runtime{Auth: app.AuthOperations{OpenSecretsStore: func() (secrets.Store, error) {
		t.Error("managed list opened local secrets")
		return nil, errors.New("locked keychain")
	}}})
	if result.err != nil {
		t.Fatal(result.err)
	}
	var report struct {
		Accounts []struct{ Email string } `json:"accounts"`
	}
	if err := json.Unmarshal([]byte(result.stdout), &report); err != nil {
		t.Fatal(err)
	}
	if len(report.Accounts) != 1 || report.Accounts[0].Email != "remote@example.com" {
		t.Fatalf("remote accounts missing: %s", result.stdout)
	}
}

func TestManagedAuthRejectsLocalCredentialEnrollment(t *testing.T) {
	for _, command := range []string{"credentials", "import", "tokens", "keyring", "service-account", "keep"} {
		result := executeWithTestRuntime(t, []string{"auth", command}, nil)
		if result.err == nil || !strings.Contains(result.err.Error(), "unexpected argument") {
			t.Errorf("obsolete auth %s remains reachable: %v", command, result.err)
		}
	}
}

func TestManagedUpdateDoesNotRecommendUpstreamBinary(t *testing.T) {
	result := executeWithTestRuntime(t, []string{"--json", "update", "status"}, nil)
	if result.err != nil {
		t.Fatal(result.err)
	}
	if strings.Contains(result.stdout, "github.com/openclaw/gogcli/releases") || !strings.Contains(result.stdout, "source") {
		t.Fatalf("incorrect fork update advice: %s", result.stdout)
	}
}

func TestManagedDoctorSeparatesRecordedVerificationFromCallerChecks(t *testing.T) {
	for _, denied := range []bool{false, true} {
		t.Run(fmt.Sprint(denied), func(t *testing.T) {
			paths := []string{}
			inventory := `{"accounts":[{"email":"remote@example.com","connection_status":"connected","services":[{"name":"gmail","authorization_status":"granted","last_verification_status":"succeeded","last_verified_at":123},{"name":"drive","authorization_status":"granted","last_verification_status":"succeeded","last_verified_at":124}]}],"setup_url":"https://example.com/onboarding/"}`
			home := managedProxyFixture(t, inventory, func(w http.ResponseWriter, r *http.Request) {
				paths = append(paths, r.URL.Path)
				if r.Method != http.MethodGet || r.Header.Get("X-GOG-Account") != "remote@example.com" || r.Header.Get("X-GOG-Google-Host") == "" {
					t.Errorf("invalid caller probe: %s %s", r.Method, r.URL.Path)
				}
				if r.URL.Query().Get("fields") == "" {
					t.Error("probe omitted response field bound")
				}
				if strings.HasSuffix(r.URL.Path, "/messages") && r.URL.Query().Get("maxResults") != "1" {
					t.Error("Gmail probe not bounded")
				}
				if strings.HasSuffix(r.URL.Path, "/files") && r.URL.Query().Get("pageSize") != "1" {
					t.Error("Drive probe not bounded")
				}
				if denied && strings.Contains(r.URL.Path, "/drive/") {
					w.WriteHeader(http.StatusForbidden)
					fmt.Fprint(w, `{"error":{"code":403,"message":"SENSITIVE content from a response"}}`)
					return
				}
				switch {
				case strings.HasSuffix(r.URL.Path, "/profile"):
					fmt.Fprint(w, `{"emailAddress":"remote@example.com"}`)
				case strings.HasSuffix(r.URL.Path, "/about"):
					fmt.Fprint(w, `{"user":{"emailAddress":"remote@example.com"}}`)
				default:
					fmt.Fprint(w, `{}`)
				}
			})
			result := executeWithTestRuntime(t, []string{"--home", home, "--json", "auth", "doctor", "--check"}, &app.Runtime{Auth: app.AuthOperations{OpenSecretsStore: func() (secrets.Store, error) {
				t.Error("doctor opened local keyring")
				return nil, errors.New("locked keyring")
			}}})
			if (result.err != nil) != denied {
				t.Fatalf("doctor error = %v, denied = %v, output = %s", result.err, denied, result.stdout)
			}
			var report struct {
				Status string            `json:"status"`
				Checks []authDoctorCheck `json:"checks"`
			}
			if err := json.Unmarshal([]byte(result.stdout), &report); err != nil {
				t.Fatal(err)
			}
			foundGmail, foundDrive := false, false
			for _, check := range report.Checks {
				if check.Name == "caller.gmail" {
					foundGmail = check.Status == doctorOK
				}
				if check.Name == "caller.drive" {
					foundDrive = check.Status == doctorOK && !denied || check.Status == doctorError && denied
				}
				if check.Name == "recorded.drive" && check.Status != doctorOK {
					t.Error("caller denial rewrote recorded verification")
				}
			}
			if !foundGmail || !foundDrive || len(paths) < 3 || strings.Contains(result.stdout, "SENSITIVE") {
				t.Fatalf("incorrect checks: %s; paths=%v", result.stdout, paths)
			}
		})
	}
}

func TestManagedAutomaticAccountSelectionUsesRemoteInventory(t *testing.T) {
	for _, count := range []int{0, 1, 2} {
		t.Run(fmt.Sprint(count), func(t *testing.T) {
			accounts := []map[string]any{}
			for i := 0; i < count; i++ {
				accounts = append(accounts, map[string]any{"email": fmt.Sprintf("remote%d@example.com", i), "connection_status": "connected", "services": []any{}})
			}
			payload, err := json.Marshal(map[string]any{"accounts": accounts, "setup_url": "https://example.com/onboarding/"})
			if err != nil {
				t.Fatal(err)
			}
			home := managedProxyFixture(t, string(payload), nil)
			gotAccount := ""
			stop := errors.New("stop after actual managed selection")
			result := executeWithTestRuntime(t, []string{"--home", home, "--account", "auto", "drive", "ls"}, &app.Runtime{Services: app.Services{Drive: func(_ context.Context, account string) (*drive.Service, error) {
				gotAccount = account
				return nil, stop
			}}})
			if count == 1 {
				if !errors.Is(result.err, stop) || gotAccount != "remote0@example.com" {
					t.Fatalf("selected %q: %v", gotAccount, result.err)
				}
			} else if result.err == nil || gotAccount != "" {
				t.Fatalf("ambiguous inventory selected %q: %v", gotAccount, result.err)
			}
		})
	}
}

func TestManagedOwnerCommandsDoNotChangeAccess(t *testing.T) {
	for _, args := range [][]string{{"auth", "add", "owner@example.com"}, {"auth", "setup"}, {"auth", "manage"}, {"auth", "remove", "owner@example.com"}} {
		result := executeWithTestRuntime(t, append([]string{"--home", t.TempDir(), "--json"}, args...), &app.Runtime{Auth: app.AuthOperations{OpenSecretsStore: func() (secrets.Store, error) {
			t.Error("owner command touched local tokens")
			return nil, errors.New("locked keyring")
		}}})
		if result.err != nil {
			t.Fatal(result.err)
		}
		var report struct {
			URL     string `json:"setup_url"`
			Changed bool   `json:"changed"`
		}
		if err := json.Unmarshal([]byte(result.stdout), &report); err != nil {
			t.Fatal(err)
		}
		if report.URL != canonicalSetupURL || report.Changed {
			t.Fatalf("owner command misrepresented result: %s", result.stdout)
		}
	}
}

func TestManagedCallerCheckRejectsWrongGoogleIdentity(t *testing.T) {
	for _, service := range []string{"gmail", "drive"} {
		t.Run(service, func(t *testing.T) {
			inventory := fmt.Sprintf(`{"accounts":[{"email":"remote@example.com","connection_status":"connected","services":[{"name":%q,"authorization_status":"granted","last_verification_status":"succeeded","last_verified_at":123}]}],"setup_url":"https://example.com/onboarding/"}`, service)
			reads := 0
			home := managedProxyFixture(t, inventory, func(w http.ResponseWriter, r *http.Request) {
				reads++
				switch {
				case strings.HasSuffix(r.URL.Path, "/profile"):
					fmt.Fprint(w, `{"emailAddress":"different@example.com"}`)
				case strings.HasSuffix(r.URL.Path, "/about"):
					fmt.Fprint(w, `{"user":{"emailAddress":"different@example.com"}}`)
				default:
					t.Errorf("continued content listing after identity mismatch: %s", r.URL.Path)
				}
			})
			result := executeWithTestRuntime(t, []string{"--home", home, "--json", "auth", "doctor", "--check"}, nil)
			if result.err == nil || !strings.Contains(result.stdout, "unexpected Google account identity") || reads != 1 {
				t.Fatalf("incorrect identity result: error=%v reads=%d output=%s", result.err, reads, result.stdout)
			}
		})
	}
}

func TestManagedStatusReportsRemoteFactsWithoutLocalSecrets(t *testing.T) {
	home := managedProxyFixture(t, `{"accounts":[{"email":"remote@example.com","connection_status":"connected","services":[]}],"setup_url":"https://example.com/onboarding/"}`, nil)
	result := executeWithTestRuntime(t, []string{"--home", home, "--json", "auth", "status"}, &app.Runtime{Auth: app.AuthOperations{OpenSecretsStore: func() (secrets.Store, error) {
		t.Error("status opened local secrets")
		return nil, errors.New("locked keychain")
	}}})
	if result.err != nil {
		t.Fatal(result.err)
	}
	var report struct {
		Account    string `json:"account"`
		Accessible bool   `json:"inventory_accessible"`
		Proxy      struct {
			Source  string `json:"credential_source"`
			BaseURL string `json:"base_url"`
		} `json:"proxy"`
	}
	if err := json.Unmarshal([]byte(result.stdout), &report); err != nil {
		t.Fatal(err)
	}
	if report.Account != "remote@example.com" || !report.Accessible || report.Proxy.Source == "" || !strings.HasSuffix(report.Proxy.BaseURL, "/stage/") || strings.Contains(result.stdout, "fixture-key") || strings.Contains(result.stdout, "keyring") {
		t.Fatalf("status did not report safe remote facts: %s", result.stdout)
	}
}

func TestManagedDoctorDoesNotReportDisconnectedAccountHealthy(t *testing.T) {
	home := managedProxyFixture(t, `{"accounts":[{"email":"remote@example.com","connection_status":"not_connected","services":[]}],"setup_url":"https://example.com/onboarding/"}`, nil)
	result := executeWithTestRuntime(t, []string{"--home", home, "--json", "auth", "doctor"}, nil)
	if result.err != nil {
		t.Fatal(result.err)
	}
	var report struct {
		Status string            `json:"status"`
		Checks []authDoctorCheck `json:"checks"`
	}
	if err := json.Unmarshal([]byte(result.stdout), &report); err != nil {
		t.Fatal(err)
	}
	if report.Status != doctorWarn {
		t.Fatalf("unconnected account reported healthy: %s", result.stdout)
	}
}
