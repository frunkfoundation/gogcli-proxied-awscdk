package cmd

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"testing"

	"google.golang.org/api/calendar/v3"

	"github.com/openclaw/gogcli/internal/app"
	"github.com/openclaw/gogcli/internal/config"
	"github.com/openclaw/gogcli/internal/googleapi"
	"github.com/openclaw/gogcli/internal/secrets"
)

type fakeSecretsStore struct {
	defaultAccount string
	tokens         []secrets.Token
	errDefault     error
	errListTokens  error
}

func (s *fakeSecretsStore) Keys() ([]string, error) { return nil, errors.New("not implemented") }
func (s *fakeSecretsStore) SetToken(string, string, secrets.Token) error {
	return errors.New("not implemented")
}

func (s *fakeSecretsStore) GetToken(string, string) (secrets.Token, error) {
	return secrets.Token{}, errors.New("not implemented")
}

func (s *fakeSecretsStore) DeleteToken(string, string) error { return errors.New("not implemented") }

func (s *fakeSecretsStore) SetDefaultAccount(string, string) error {
	return errors.New("not implemented")
}

func (s *fakeSecretsStore) GetDefaultAccount(string) (string, error) {
	return s.defaultAccount, s.errDefault
}

func (s *fakeSecretsStore) ListTokens() ([]secrets.Token, error) { return s.tokens, s.errListTokens }

func TestRequireAccount_PrefersFlag(t *testing.T) {
	t.Setenv("GOG_ACCOUNT", "env@example.com")
	flags := &RootFlags{Account: "flag@example.com"}
	got, err := requireAccount(flags)
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	if got != "flag@example.com" {
		t.Fatalf("got %q", got)
	}
}

func TestRequireAccount_UsesEnv(t *testing.T) {
	t.Setenv("GOG_ACCOUNT", "env@example.com")
	flags := &RootFlags{}
	got, err := requireAccount(flags)
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	if got != "env@example.com" {
		t.Fatalf("got %q", got)
	}
}

func TestRequireAccount_UsesConfigDefaultAccount(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "xdg-config"))
	t.Setenv("GOG_ACCOUNT", "")
	if err := defaultConfigStoreForTest(t).Write(config.File{
		DefaultAccount: "config@example.com",
	}); err != nil {
		t.Fatalf("write config: %v", err)
	}

	got, err := requireAccount(&RootFlags{configStoreResolver: func() (*config.ConfigStore, error) { return defaultConfigStoreForTest(t), nil }})
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	if got != "config@example.com" {
		t.Fatalf("got %q", got)
	}
}

func TestRequireAccount_ConfigBeforeEnv(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "xdg-config"))
	t.Setenv("GOG_ACCOUNT", "env@example.com")
	if err := defaultConfigStoreForTest(t).Write(config.File{
		DefaultAccount: "config@example.com",
	}); err != nil {
		t.Fatalf("write config: %v", err)
	}

	got, err := requireAccount(&RootFlags{configStoreResolver: func() (*config.ConfigStore, error) { return defaultConfigStoreForTest(t), nil }})
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	if got != "config@example.com" {
		t.Fatalf("got %q", got)
	}
}

func TestRequireAccount_ResolvesAliasFlag(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "xdg-config"))
	if err := defaultConfigStoreForTest(t).Write(config.File{
		AccountAliases: map[string]string{"work": "alias@example.com"},
	}); err != nil {
		t.Fatalf("write config: %v", err)
	}

	flags := &RootFlags{
		Account:             "work",
		configStoreResolver: func() (*config.ConfigStore, error) { return defaultConfigStoreForTest(t), nil },
	}
	got, err := requireAccount(flags)
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	if got != "alias@example.com" {
		t.Fatalf("got %q", got)
	}
}

func TestRequireAccount_ResolvesAliasEnv(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "xdg-config"))
	if err := defaultConfigStoreForTest(t).Write(config.File{
		AccountAliases: map[string]string{"work": "alias@example.com"},
	}); err != nil {
		t.Fatalf("write config: %v", err)
	}

	t.Setenv("GOG_ACCOUNT", "work")
	flags := &RootFlags{
		configStoreResolver: func() (*config.ConfigStore, error) { return defaultConfigStoreForTest(t), nil },
	}
	got, err := requireAccount(flags)
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	if got != "alias@example.com" {
		t.Fatalf("got %q", got)
	}
}

func TestRequireAccount_ADCUsesCapturedMode(t *testing.T) {
	t.Setenv("GOG_ACCOUNT", "")

	got, err := requireAccount(&RootFlags{authMode: googleapi.AuthModeADC})
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	if got != "adc" {
		t.Fatalf("got %q", got)
	}
}

func TestRequireAccount_ADCEnvAutoUsesPlaceholder(t *testing.T) {
	for _, value := range []string{"auto", "default"} {
		t.Run(value, func(t *testing.T) {
			t.Setenv("GOG_ACCOUNT", value)

			got, err := requireAccount(&RootFlags{authMode: googleapi.AuthModeADC})
			if err != nil {
				t.Fatalf("err: %v", err)
			}
			if got != adcPlaceholderAccount {
				t.Fatalf("got %q", got)
			}
		})
	}
}

func TestRequireAccount_Missing(t *testing.T) {
	t.Setenv("GOG_ACCOUNT", "")
	flags := &RootFlags{}
	_, err := requireAccount(flags)
	if err == nil {
		t.Fatalf("expected error")
	}
}

func TestRequireAccount_AccessTokenNoAccount(t *testing.T) {
	t.Setenv("GOG_ACCOUNT", "")
	var diagnostics strings.Builder
	flags := &RootFlags{AccessToken: "ya29.some-token", diagnostics: &diagnostics}
	flags.authOperations = app.AuthOperations{
		OpenSecretsStore: func() (secrets.Store, error) {
			t.Fatal("OpenSecretsStore should not be called when access token is provided")
			return nil, errors.New("unreachable")
		},
	}

	got, err := requireAccount(flags)
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	if got != accessTokenPlaceholderAccount {
		t.Fatalf("got %q", got)
	}
	if !strings.Contains(diagnostics.String(), directAccessTokenWarning) {
		t.Fatalf("expected warning, got %q", diagnostics.String())
	}
}

func TestRequireAccount_AccessTokenWithExplicitAccount(t *testing.T) {
	var diagnostics strings.Builder
	flags := &RootFlags{
		AccessToken: "ya29.some-token",
		Account:     "explicit@example.com",
		diagnostics: &diagnostics,
	}

	got, err := requireAccount(flags)
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	if got != "explicit@example.com" {
		t.Fatalf("got %q", got)
	}
	if !strings.Contains(diagnostics.String(), directAccessTokenWarning) {
		t.Fatalf("expected warning, got %q", diagnostics.String())
	}
}

func TestExecuteAccountAliasUsesRuntimeConfigStore(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "xdg-config"))

	ambientStore := defaultConfigStoreForTest(t)
	if err := ambientStore.SetAccountAlias("work", "ambient@example.com"); err != nil {
		t.Fatalf("set ambient alias: %v", err)
	}
	runtimeStore := config.NewConfigStore(config.Layout{ConfigDir: t.TempDir()})
	if err := runtimeStore.SetAccountAlias("work", "runtime@example.com"); err != nil {
		t.Fatalf("set runtime alias: %v", err)
	}

	wantErr := errors.New("stop after account resolution")
	var gotAccount string
	result := executeWithTestRuntime(t, []string{
		"--account", "work",
		"calendar", "time",
	}, &app.Runtime{
		Config: runtimeStore,
		Services: app.Services{
			Calendar: func(_ context.Context, account string) (*calendar.Service, error) {
				gotAccount = account
				return nil, wantErr
			},
		},
	})
	if !errors.Is(result.err, wantErr) {
		t.Fatalf("error = %v, want %v", result.err, wantErr)
	}
	if gotAccount != "runtime@example.com" {
		t.Fatalf("account = %q, want runtime alias target", gotAccount)
	}
}

func TestRequireAccount_DoesNotInferFromLocalTokens(t *testing.T) {
	t.Setenv("GOG_ACCOUNT", "")
	for _, tc := range []struct {
		name  string
		store *fakeSecretsStore
	}{
		{"default", &fakeSecretsStore{defaultAccount: "local@example.com"}},
		{"single token", &fakeSecretsStore{tokens: []secrets.Token{{Email: "local@example.com", Client: config.DefaultClientName}}}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			calls := 0
			flags := &RootFlags{authOperations: app.AuthOperations{OpenSecretsStore: func() (secrets.Store, error) {
				calls++
				return tc.store, nil
			}}}
			got, err := requireAccount(flags)
			if err == nil || calls != 0 {
				t.Fatalf("account = %q, error = %v, local store calls = %d; want missing account without local inference", got, err, calls)
			}
		})
	}
}

func TestRequireAccount_AutoAndDefaultRejected(t *testing.T) {
	t.Setenv("GOG_ACCOUNT", "")
	for _, value := range []string{"auto", "default", "AUTO", " Default "} {
		if _, err := requireAccount(&RootFlags{Account: value}); err == nil {
			t.Fatalf("expected missing account for %q", value)
		}
	}
}
