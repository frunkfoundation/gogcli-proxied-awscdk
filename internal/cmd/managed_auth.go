package cmd

import (
	"context"
	"fmt"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/openclaw/gogcli/internal/googleapi"
	"github.com/openclaw/gogcli/internal/outfmt"
)

const (
	managedDefaultTimeout = 15 * time.Second
	canonicalSetupURL     = "https://gp-039168171bf05570eadba6a8.k-rae.com/prd/onboarding/"
)

type ManagedAuthListCmd struct {
	Check   bool          `name:"check" help:"Check Gmail and Drive reads as this caller; recorded verification is separate"`
	Timeout time.Duration `name:"timeout" help:"Timeout for inventory and each service check" default:"15s"`
}

type managedAccountReport struct {
	googleapi.ManagedAccount
	CallerChecks []managedCallerCheck `json:"caller_checks,omitempty"`
}

func (c *ManagedAuthListCmd) Run(ctx context.Context, _ *RootFlags) error {
	inventory, err := managedInventory(ctx, c.Timeout)
	if err != nil {
		return err
	}
	accounts := make([]managedAccountReport, 0, len(inventory.Accounts))
	failed := false
	for _, account := range inventory.Accounts {
		report := managedAccountReport{ManagedAccount: account}
		if c.Check {
			report.CallerChecks = managedChecks(ctx, account, nil, c.Timeout)
			failed = failed || callerChecksFailed(report.CallerChecks)
		}
		accounts = append(accounts, report)
	}
	if outfmt.IsJSON(ctx) {
		err = outfmt.WriteJSON(ctx, stdoutWriter(ctx), map[string]any{"accounts": accounts, "setup_url": inventory.SetupURL})
	} else {
		w, done := tableWriter(ctx)
		defer done()
		fmt.Fprintln(w, "EMAIL\tCONNECTION\tSERVICE\tAUTHORIZATION\tLAST_VERIFICATION\tLAST_VERIFIED_AT")
		for _, account := range accounts {
			if len(account.Services) == 0 {
				fmt.Fprintf(w, "%s\t%s\t\t%s\t%s\t0\n", account.Email, account.ConnectionStatus, "unknown", "unknown")
			}
			for _, service := range account.Services {
				fmt.Fprintf(w, "%s\t%s\t%s\t%s\t%s\t%d\n", account.Email, account.ConnectionStatus, service.Name, service.AuthorizationStatus, service.LastVerificationStatus, service.LastVerifiedAt)
			}
			for _, check := range account.CallerChecks {
				fmt.Fprintf(w, "caller_check\t%s\t%s\t%s\t%s\n", account.Email, check.Service, check.Status, check.Detail)
			}
		}
	}
	if err != nil {
		return err
	}
	if failed {
		return fmt.Errorf("one or more caller service checks failed; recorded connection verification does not grant caller permission")
	}
	return nil
}

type ManagedAuthStatusCmd struct{}

func (c *ManagedAuthStatusCmd) Run(ctx context.Context, flags *RootFlags) error {
	bounded, cancel := context.WithTimeout(ctx, managedDefaultTimeout)
	defer cancel()
	info, err := googleapi.ProxyConnection(bounded)
	if err != nil {
		return err
	}
	inventory, err := googleapi.ListManagedAccounts(bounded)
	if err != nil {
		return err
	}
	account, selectionErr := selectManagedAccount(flags, inventory)
	report := map[string]any{"proxy": info, "inventory_accessible": true, "account": account, "setup_url": inventory.SetupURL}
	if selectionErr != nil {
		report["selection_error"] = selectionErr.Error()
	}
	if outfmt.IsJSON(ctx) {
		return outfmt.WriteJSON(ctx, stdoutWriter(ctx), report)
	}
	fmt.Fprintf(stdoutWriter(ctx), "proxy_url\t%s\naws_profile\t%s\ncredential_source\t%s\ninventory_accessible\ttrue\naccount\t%s\n", info.BaseURL, info.AWSProfile, info.CredentialSource, account)
	if selectionErr != nil {
		fmt.Fprintf(stdoutWriter(ctx), "selection_error\t%s\n", selectionErr)
	}
	return nil
}

type ManagedAuthAddCmd struct {
	Email string `arg:"" name:"email" help:"Account to connect through the owner page"`
}
type (
	ManagedAuthSetupCmd  struct{}
	ManagedAuthManageCmd struct{}
	ManagedAuthRemoveCmd struct {
		Email string `arg:"" name:"email" help:"Account whose remote access should be removed"`
	}
)

func (c *ManagedAuthAddCmd) Run(ctx context.Context) error {
	return writeManagedSetup(ctx, c.Email, "Connect this account on the owner page. Google credentials remain on the server.")
}

func (c *ManagedAuthSetupCmd) Run(ctx context.Context) error {
	return writeManagedSetup(ctx, "", "Prepare Google services and connect accounts on the owner page.")
}

func (c *ManagedAuthManageCmd) Run(ctx context.Context) error {
	return writeManagedSetup(ctx, "", "Manage connections and caller access on the owner page.")
}

func (c *ManagedAuthRemoveCmd) Run(ctx context.Context) error {
	return writeManagedSetup(ctx, c.Email, "Use Remove access or Disconnect on the owner page. This command has not removed remote access.")
}

func writeManagedSetup(ctx context.Context, email, instruction string) error {
	store, err := commandConfigStore(ctx)
	if err != nil {
		return err
	}
	cfg, err := store.Read()
	if err != nil {
		return err
	}
	base := strings.TrimSpace(cfg.ProxyBaseURL)
	if base == "" {
		base = strings.TrimSpace(os.Getenv("GOG_PROXY_BASE_URL"))
	}
	setupURL := canonicalSetupURL
	if base != "" {
		parsed, parseErr := url.Parse(base)
		if parseErr != nil || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" || (parsed.Scheme != "https" && parsed.Scheme != "http") {
			return usage("invalid proxy base URL for owner setup")
		}
		setupURL = strings.TrimRight(base, "/") + "/onboarding/"
	}
	if outfmt.IsJSON(ctx) {
		return outfmt.WriteJSON(ctx, stdoutWriter(ctx), map[string]any{"setup_url": setupURL, "email": strings.TrimSpace(email), "instruction": instruction, "changed": false})
	}
	fmt.Fprintf(stdoutWriter(ctx), "%s\n%s\n", instruction, setupURL)
	if strings.TrimSpace(email) != "" {
		fmt.Fprintf(stdoutWriter(ctx), "account\t%s\n", strings.TrimSpace(email))
	}
	return nil
}

func managedInventory(ctx context.Context, timeout time.Duration) (googleapi.AccountInventory, error) {
	if timeout <= 0 {
		return googleapi.AccountInventory{}, usage("timeout must be positive")
	}
	bounded, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	return googleapi.ListManagedAccounts(bounded)
}

func selectManagedAccount(flags *RootFlags, inventory googleapi.AccountInventory) (string, error) {
	account, selected, err := configuredAccount(flags)
	if err != nil {
		return "", err
	}
	if selected {
		for _, candidate := range inventory.Accounts {
			if strings.EqualFold(candidate.Email, account) {
				return candidate.Email, nil
			}
		}
		return "", usage("selected account is not in this caller's remote inventory; check remote account access")
	}
	if len(inventory.Accounts) == 1 {
		return inventory.Accounts[0].Email, nil
	}
	if len(inventory.Accounts) == 0 {
		return "", usage("no remote accounts available; connect an account and grant this caller access on the owner page")
	}
	return "", usage("multiple remote accounts available; select --account or set default_account")
}
