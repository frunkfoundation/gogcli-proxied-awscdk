package googleapi

import (
	"context"
	"crypto/tls"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"google.golang.org/api/option"

	"github.com/openclaw/gogcli/internal/authclient"
	"github.com/openclaw/gogcli/internal/googleauth"
)

const (
	// responseHeaderTimeout limits the time waiting for the server to begin
	// responding (send response headers). Once headers arrive and the body
	// starts streaming, there is no hard cap — large file downloads are not
	// cut short. This replaces the former http.Client.Timeout which applied
	// to the entire request lifecycle and caused timeouts on large Drive
	// file downloads.
	responseHeaderTimeout = 30 * time.Second
)

func optionsForAccount(ctx context.Context, service googleauth.Service, email string) ([]option.ClientOption, error) {
	scopes, err := googleauth.Scopes(service)
	if err != nil {
		return nil, fmt.Errorf("resolve scopes: %w", err)
	}

	return optionsForAccountScopes(ctx, string(service), email, scopes)
}

type googleServiceFactory[T any] func(context.Context, ...option.ClientOption) (*T, error)

func newGoogleServiceForAccount[T any](
	ctx context.Context,
	email string,
	service googleauth.Service,
	label string,
	factory googleServiceFactory[T],
) (*T, error) {
	opts, err := optionsForAccount(ctx, service, email)
	if err != nil {
		return nil, fmt.Errorf("%s options: %w", label, err)
	}

	return newGoogleService(ctx, label, opts, factory)
}

func newGoogleServiceForScopes[T any](
	ctx context.Context,
	email string,
	serviceLabel string,
	errorLabel string,
	scopes []string,
	factory googleServiceFactory[T],
) (*T, error) {
	opts, err := optionsForAccountScopes(ctx, serviceLabel, email, scopes)
	if err != nil {
		return nil, fmt.Errorf("%s options: %w", errorLabel, err)
	}

	return newGoogleService(ctx, errorLabel, opts, factory)
}

func newGoogleServiceForRequiredScopes[T any](
	ctx context.Context,
	email string,
	serviceLabel string,
	errorLabel string,
	scopes []string,
	factory googleServiceFactory[T],
) (*T, error) {
	opts, err := optionsForAccountScopesRequiringStoredGrant(ctx, serviceLabel, email, scopes)
	if err != nil {
		return nil, fmt.Errorf("%s options: %w", errorLabel, err)
	}

	return newGoogleService(ctx, errorLabel, opts, factory)
}

func newGoogleServiceForServiceAccountScopes[T any](
	ctx context.Context,
	email string,
	serviceLabel string,
	errorLabel string,
	scopes []string,
	factory googleServiceFactory[T],
) (*T, error) {
	opts, err := optionsForServiceAccountScopes(ctx, serviceLabel, email, scopes)
	if err != nil {
		return nil, fmt.Errorf("%s options: %w", errorLabel, err)
	}

	return newGoogleService(ctx, errorLabel, opts, factory)
}

func newGoogleService[T any](
	ctx context.Context,
	label string,
	opts []option.ClientOption,
	factory googleServiceFactory[T],
) (*T, error) {
	svc, err := factory(ctx, opts...)
	if err != nil {
		return nil, fmt.Errorf("create %s service: %w", label, err)
	}

	return svc, nil
}

func proxyTransportForAccount(ctx context.Context, email string) (http.RoundTripper, error) {
	if dependencies, ok := authDependenciesFromContext(ctx); ok && dependencies.Mode == AuthModeADC {
		return nil, errProxyADCUnsupported
	}

	if authclient.AccessTokenFromContext(ctx) != "" {
		return nil, errProxyAccessTokenUnsupported
	}

	proxyCfg, err := loadProxyConfig(ctx)
	if err != nil {
		return nil, err
	}

	provider, err := loadAWSCredentialsProvider(ctx, proxyCfg.Region, proxyCfg.AWSProfile)
	if err != nil {
		return nil, err
	}
	transport := NewRetryTransport(quotaProjectTransportFromContext(ctx, &apiGatewayProxyTransport{
		Base: newBaseTransport(), Endpoint: proxyCfg.Endpoint, Region: proxyCfg.Region,
		APIKey: proxyCfg.APIKey, Account: email, CredentialsProvider: provider,
	}))

	return readOnlyTransportFromContext(ctx, transport), nil
}

func optionsForAccountScopes(ctx context.Context, serviceLabel string, email string, scopes []string) ([]option.ClientOption, error) {
	slog.Debug("creating proxy client options", "serviceLabel", serviceLabel, "email", email, "scopeCount", len(scopes))
	return proxyClientOptions(ctx, serviceLabel, email)
}

func optionsForAccountScopesRequiringStoredGrant(ctx context.Context, serviceLabel string, email string, scopes []string) ([]option.ClientOption, error) {
	return optionsForAccountScopes(ctx, serviceLabel, email, scopes)
}

func optionsForServiceAccountScopes(ctx context.Context, serviceLabel string, email string, scopes []string) ([]option.ClientOption, error) {
	return optionsForAccountScopes(ctx, serviceLabel, email, scopes)
}

func proxyClientOptions(ctx context.Context, serviceLabel string, email string) ([]option.ClientOption, error) {
	slog.Debug("creating client options with custom scopes", "serviceLabel", serviceLabel, "email", email)

	transport, err := proxyTransportForAccount(ctx, email)
	if err != nil {
		return nil, err
	}

	c := &http.Client{
		Transport: transport,
		// No Timeout set: large file downloads (Drive videos, etc.) must not
		// be cut short. Server responsiveness is guarded by the transport's
		// ResponseHeaderTimeout instead.
	}

	slog.Debug("client options with custom scopes created successfully", "serviceLabel", serviceLabel, "email", email)

	return []option.ClientOption{option.WithHTTPClient(c)}, nil
}

// NewHTTPClient returns a raw *http.Client authenticated for the given service
// and account. The caller may set CheckRedirect or other policies on the
// returned client.
func NewHTTPClient(ctx context.Context, service googleauth.Service, email string) (*http.Client, error) {
	_, err := googleauth.Scopes(service)
	if err != nil {
		return nil, fmt.Errorf("resolve scopes: %w", err)
	}

	transport, err := proxyTransportForAccount(ctx, email)
	if err != nil {
		return nil, err
	}

	return &http.Client{Transport: transport}, nil
}

// NewHTTPClientForScopes returns an authenticated raw client for a
// Discovery-described API method whose scopes are not part of the static
// service registry.
func NewHTTPClientForScopes(ctx context.Context, serviceLabel, email string, scopes []string) (*http.Client, error) {
	transport, err := proxyTransportForAccount(ctx, email)
	if err != nil {
		return nil, err
	}

	return &http.Client{Transport: transport}, nil
}

func newBaseTransport() *http.Transport {
	defaultTransport, ok := http.DefaultTransport.(*http.Transport)
	if !ok || defaultTransport == nil {
		return &http.Transport{
			Proxy: http.ProxyFromEnvironment,
			TLSClientConfig: &tls.Config{
				MinVersion: tls.VersionTLS12,
			},
			ResponseHeaderTimeout: responseHeaderTimeout,
			DisableCompression:    true,
		}
	}

	// Clone() deep-copies TLSClientConfig, so no additional clone needed.
	transport := defaultTransport.Clone()
	transport.ResponseHeaderTimeout = responseHeaderTimeout
	transport.DisableCompression = true

	if transport.TLSClientConfig == nil {
		transport.TLSClientConfig = &tls.Config{MinVersion: tls.VersionTLS12}
		return transport
	}

	if transport.TLSClientConfig.MinVersion < tls.VersionTLS12 {
		transport.TLSClientConfig.MinVersion = tls.VersionTLS12
	}

	return transport
}

// NewBoundedHTTPClient returns an unauthenticated client with the same
// ResponseHeaderTimeout used by authenticated Google clients. It does not
// set Client.Timeout so large downloads are not cut short after headers
// arrive.
func NewBoundedHTTPClient() *http.Client {
	return &http.Client{Transport: newBaseTransport()}
}
