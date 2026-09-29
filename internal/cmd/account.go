package cmd

import (
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/openclaw/gogcli/internal/config"
	"github.com/openclaw/gogcli/internal/googleapi"
)

const (
	accessTokenPlaceholderAccount = "access-token-user"
	adcPlaceholderAccount         = "adc"
	directAccessTokenWarning      = "Note: Using direct access token (expires in ~1 hour; no auto-refresh)" //nolint:gosec // user-facing warning text, not a credential
)

func requireAccount(flags *RootFlags) (string, error) {
	// In ADC mode the service account authenticates as itself — no user email
	// or keyring lookup is needed. We still accept --account/GOG_ACCOUNT as an
	// optional label (e.g. for logging), but it is not required.
	if isADCAuthMode(flags) {
		if v := flagAccount(flags); v != "" {
			if shouldAutoSelectAccount(v) {
				return adcPlaceholderAccount, nil
			}
			return v, nil
		}
		if v := strings.TrimSpace(os.Getenv("GOG_ACCOUNT")); v != "" {
			if shouldAutoSelectAccount(v) {
				return adcPlaceholderAccount, nil
			}
			return v, nil
		}
		return adcPlaceholderAccount, nil
	}

	if account, ok, err := configuredAccount(flags); err != nil {
		return "", err
	} else if ok {
		return finalizeRequiredAccount(flags, account), nil
	}

	if hasDirectAccessToken(flags) {
		return finalizeRequiredAccount(flags, accessTokenPlaceholderAccount), nil
	}

	return "", usage("missing --account (or set default_account in config.json or GOG_ACCOUNT)")
}

func isADCAuthMode(flags *RootFlags) bool {
	return flags != nil && flags.authMode == googleapi.AuthModeADC
}

func configuredAccount(flags *RootFlags) (string, bool, error) {
	if candidate := flagAccount(flags); candidate != "" {
		account, ok, err := selectConfiguredAccount(flags, candidate)
		if err != nil {
			return "", false, err
		}
		return account, ok, nil
	}

	if flags != nil && flags.configStoreResolver != nil {
		store, err := accountConfigStore(flags)
		if err != nil {
			return "", false, err
		}
		cfg, err := store.Read()
		if err != nil {
			return "", false, err
		}
		if candidate := strings.TrimSpace(cfg.DefaultAccount); candidate != "" {
			account, ok, err := selectConfiguredAccount(flags, candidate)
			if err != nil || ok {
				return account, ok, err
			}
		}
	}

	if candidate := strings.TrimSpace(os.Getenv("GOG_ACCOUNT")); candidate != "" {
		account, ok, err := selectConfiguredAccount(flags, candidate)
		if err != nil {
			return "", false, err
		}
		return account, ok, nil
	}

	return "", false, nil
}

func flagAccount(flags *RootFlags) string {
	if flags == nil {
		return ""
	}

	return strings.TrimSpace(flags.Account)
}

func selectConfiguredAccount(flags *RootFlags, value string) (string, bool, error) {
	if resolved, ok, err := resolveAccountAlias(flags, value); err != nil {
		return "", false, err
	} else if ok {
		return resolved, true, nil
	}

	value = strings.TrimSpace(value)
	if value == "" || shouldAutoSelectAccount(value) {
		return "", false, nil
	}

	return value, true, nil
}

func directAccessToken(flags *RootFlags) string {
	if flags == nil {
		return ""
	}

	return strings.TrimSpace(flags.AccessToken)
}

func hasDirectAccessToken(flags *RootFlags) bool {
	return directAccessToken(flags) != ""
}

func finalizeRequiredAccount(flags *RootFlags, account string) string {
	if hasDirectAccessToken(flags) {
		_, _ = fmt.Fprintln(accountDiagnostics(flags), directAccessTokenWarning)
	}

	return account
}

func accountDiagnostics(flags *RootFlags) io.Writer {
	if flags == nil || flags.diagnostics == nil {
		return io.Discard
	}
	return flags.diagnostics
}

func resolveAccountAlias(flags *RootFlags, value string) (string, bool, error) {
	value = strings.TrimSpace(value)
	if value == "" || strings.Contains(value, "@") || shouldAutoSelectAccount(value) {
		return "", false, nil
	}

	store, err := accountConfigStore(flags)
	if err != nil {
		return "", false, err
	}
	return store.ResolveAccountAlias(value)
}

func accountConfigStore(flags *RootFlags) (*config.ConfigStore, error) {
	if flags != nil && flags.configStoreResolver != nil {
		return flags.configStoreResolver()
	}
	return nil, errRuntimeRequired
}

func shouldAutoSelectAccount(value string) bool {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "auto", eventTypeDefault:
		return true
	default:
		return false
	}
}
