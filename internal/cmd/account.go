package cmd

import (
	"os"
	"strings"

	"github.com/openclaw/gogcli/internal/config"
	"github.com/openclaw/gogcli/internal/googleapi"
)

const (
	accessTokenPlaceholderAccount = "access-token-user"
	adcPlaceholderAccount         = "adc"
)

func requireAccount(flags *RootFlags) (string, error) {
	if isADCAuthMode(flags) {
		return "", usage("ADC is unsupported for managed Google accounts; configure proxy caller credentials")
	}
	if hasDirectAccessToken(flags) {
		return "", usage("direct Google access tokens are unsupported for managed accounts; configure proxy caller credentials")
	}
	if account, ok, err := configuredAccount(flags); err != nil {
		return "", err
	} else if ok {
		return account, nil
	}
	if flags == nil || flags.inventoryReader == nil {
		return "", usage("missing --account (or set default_account in config.json or GOG_ACCOUNT)")
	}
	inventory, err := flags.inventoryReader()
	if err != nil {
		return "", err
	}

	return selectManagedAccount(flags, inventory)
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
