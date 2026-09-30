package googleapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
)

var (
	errManagedInventory  = errors.New("managed inventory request failed")
	errInventoryTooLarge = errors.New("managed inventory exceeds response limit")
)

// AccountInventory contains caller-visible connection evidence, not blanket API permission.
type AccountInventory struct {
	Accounts []ManagedAccount `json:"accounts"`
	SetupURL string           `json:"setup_url"`
}

type ManagedAccount struct {
	Email            string           `json:"email"`
	ConnectionStatus string           `json:"connection_status"`
	Services         []ManagedService `json:"services"`
}

type ManagedService struct {
	Name                   string `json:"name"`
	AuthorizationStatus    string `json:"authorization_status"`
	LastVerificationStatus string `json:"last_verification_status"`
	LastVerifiedAt         int64  `json:"last_verified_at"`
}

type ProxyConnectionInfo struct {
	BaseURL          string `json:"base_url"`
	AWSProfile       string `json:"aws_profile,omitempty"`
	CredentialSource string `json:"credential_source"`
}

func ProxyConnection(ctx context.Context) (ProxyConnectionInfo, error) {
	cfg, err := loadProxyConfig(ctx)
	if err != nil {
		return ProxyConnectionInfo{}, err
	}

	provider, err := loadAWSCredentialsProvider(ctx, cfg.Region, cfg.AWSProfile)
	if err != nil {
		return ProxyConnectionInfo{}, err
	}

	credentials, err := provider.Retrieve(ctx)
	if err != nil {
		return ProxyConnectionInfo{}, fmt.Errorf("retrieve AWS credentials: %w", err)
	}

	return ProxyConnectionInfo{BaseURL: cfg.Endpoint, AWSProfile: cfg.AWSProfile, CredentialSource: credentials.Source}, nil
}

// ListManagedAccounts signs the reserved route without Google account/host headers.
func ListManagedAccounts(ctx context.Context) (AccountInventory, error) {
	cfg, err := loadProxyConfig(ctx)
	if err != nil {
		return AccountInventory{}, err
	}

	provider, err := loadAWSCredentialsProvider(ctx, cfg.Region, cfg.AWSProfile)
	if err != nil {
		return AccountInventory{}, err
	}
	client := &http.Client{Transport: NewRetryTransport(&apiGatewayProxyTransport{
		Base: newBaseTransport(), Endpoint: cfg.Endpoint, Region: cfg.Region,
		APIKey: cfg.APIKey, CredentialsProvider: provider, Inventory: true,
	}), CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(cfg.Endpoint, "/")+"/proxy/v1/accounts", nil)
	if err != nil {
		return AccountInventory{}, fmt.Errorf("build inventory request: %w", err)
	}

	resp, err := client.Do(req)
	if err != nil {
		return AccountInventory{}, fmt.Errorf("read managed accounts: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return AccountInventory{}, fmt.Errorf("%w: HTTP %d; check caller invocation and account access", errManagedInventory, resp.StatusCode)
	}
	const maxInventoryBytes = 6 * 1024 * 1024

	body, err := io.ReadAll(io.LimitReader(resp.Body, maxInventoryBytes+1))
	if err != nil {
		return AccountInventory{}, fmt.Errorf("read managed inventory: %w", err)
	}

	if len(body) > maxInventoryBytes {
		return AccountInventory{}, errInventoryTooLarge
	}

	var inventory AccountInventory
	if err := json.Unmarshal(body, &inventory); err != nil {
		return AccountInventory{}, fmt.Errorf("decode managed inventory: %w", err)
	}

	if inventory.Accounts == nil {
		inventory.Accounts = []ManagedAccount{}
	}

	return inventory, nil
}
