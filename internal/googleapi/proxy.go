//nolint:wsl_v5 // keep signing/canonicalization logic compact and readable.
package googleapi

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strings"
	"time"
)

const (
	envProxyBaseURL = "GOG_PROXY_BASE_URL"
	envProxyAPIKey  = "GOG_PROXY_API_KEY" //nolint:gosec // env var name, not credential material

	awsServiceExecuteAPI = "execute-api"
)

var errProxyConfig = errors.New("proxy config error")

var (
	errMissingAWSCredentials = errors.New("missing AWS credentials (set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY and AWS_SESSION_TOKEN for STS creds)")
	errNilRequest            = errors.New("nil request")
	errNilBaseTransport      = errors.New("nil base transport")
	errMissingRequestURL     = errors.New("missing request url")
	errMissingAccessKeys     = errors.New("missing access key/secret key")
	errMissingRegionService  = errors.New("missing region/service")
	errMissingPayloadHash    = errors.New("missing payload hash")
)

// ProxyConfigError indicates gogcli is missing required proxy configuration.
// This is treated as a config error (stable exit code) by the CLI.
type ProxyConfigError struct {
	Missing []string
	Invalid []string
	Cause   error
}

func (e *ProxyConfigError) Error() string {
	parts := make([]string, 0, 2)
	if len(e.Missing) > 0 {
		parts = append(parts, "missing: "+strings.Join(e.Missing, ", "))
	}
	if len(e.Invalid) > 0 {
		parts = append(parts, "invalid: "+strings.Join(e.Invalid, ", "))
	}
	msg := strings.TrimSpace(strings.Join(parts, "; "))
	if msg == "" {
		msg = "invalid proxy configuration"
	}
	return fmt.Sprintf("%s (%s)", errProxyConfig.Error(), msg)
}

func (e *ProxyConfigError) Unwrap() error {
	if e.Cause != nil {
		return e.Cause
	}
	return errProxyConfig
}

type proxyConfig struct {
	Endpoint string
	APIKey   string
	Region   string
}

func loadProxyConfig() (proxyConfig, error) {
	base := strings.TrimSpace(os.Getenv(envProxyBaseURL))
	apiKey := strings.TrimSpace(os.Getenv(envProxyAPIKey))

	var missing []string
	if base == "" {
		missing = append(missing, envProxyBaseURL)
	}
	if apiKey == "" {
		missing = append(missing, envProxyAPIKey)
	}
	if len(missing) > 0 {
		return proxyConfig{}, &ProxyConfigError{Missing: missing}
	}

	u, err := url.Parse(base)
	if err != nil {
		return proxyConfig{}, &ProxyConfigError{Invalid: []string{envProxyBaseURL}, Cause: err}
	}
	if u == nil || strings.TrimSpace(u.Scheme) == "" || strings.TrimSpace(u.Host) == "" {
		return proxyConfig{}, &ProxyConfigError{Invalid: []string{envProxyBaseURL}}
	}
	if u.RawQuery != "" || u.Fragment != "" {
		return proxyConfig{}, &ProxyConfigError{Invalid: []string{envProxyBaseURL}}
	}

	// Normalize to a base URL that ends with a slash so google.golang.org/api
	// can safely append service paths like /gmail/v1/... and /drive/v3/....
	if u.Path == "" {
		u.Path = "/"
	}
	if !strings.HasSuffix(u.Path, "/") {
		u.Path += "/"
	}
	endpoint := u.String()

	region := inferRegionFromExecuteAPIHost(u.Hostname())
	if region == "" {
		region = strings.TrimSpace(os.Getenv("AWS_REGION"))
	}
	if region == "" {
		region = strings.TrimSpace(os.Getenv("AWS_DEFAULT_REGION"))
	}
	if region == "" {
		// For custom domains, users must set AWS_REGION or AWS_DEFAULT_REGION.
		return proxyConfig{}, &ProxyConfigError{Missing: []string{"AWS_REGION (or AWS_DEFAULT_REGION)"}}
	}

	return proxyConfig{
		Endpoint: endpoint,
		APIKey:   apiKey,
		Region:   region,
	}, nil
}

func inferRegionFromExecuteAPIHost(host string) string {
	host = strings.ToLower(strings.TrimSpace(host))
	// Typical API Gateway hostname:
	//   {api-id}.execute-api.{region}.amazonaws.com
	parts := strings.Split(host, ".")
	if len(parts) < 5 {
		return ""
	}
	// ... execute-api <region> amazonaws com
	if parts[len(parts)-2] != "amazonaws" || parts[len(parts)-1] != "com" {
		return ""
	}
	if parts[len(parts)-4] != "execute-api" {
		return ""
	}
	region := strings.TrimSpace(parts[len(parts)-3])
	if region == "" {
		return ""
	}
	return region
}

type awsCredentials struct {
	AccessKeyID     string
	SecretAccessKey string
	SessionToken    string
}

func loadAWSCredentials() (awsCredentials, error) {
	if creds, ok := loadAWSCredentialsFromEnv(); ok {
		return creds, nil
	}
	if creds, ok := loadAWSCredentialsFromSharedCredentialsFile(); ok {
		return creds, nil
	}

	return awsCredentials{}, errMissingAWSCredentials
}

func loadAWSCredentialsFromEnv() (awsCredentials, bool) {
	akid := strings.TrimSpace(os.Getenv("AWS_ACCESS_KEY_ID"))
	if akid == "" {
		// Alternative name occasionally used by tooling.
		akid = strings.TrimSpace(os.Getenv("AWS_ACCESS_KEY"))
	}
	secret := strings.TrimSpace(os.Getenv("AWS_SECRET_ACCESS_KEY"))
	if secret == "" {
		secret = strings.TrimSpace(os.Getenv("AWS_SECRET_KEY"))
	}
	token := strings.TrimSpace(os.Getenv("AWS_SESSION_TOKEN"))
	if token == "" {
		token = strings.TrimSpace(os.Getenv("AWS_SECURITY_TOKEN"))
	}

	if akid == "" || secret == "" {
		return awsCredentials{}, false
	}
	return awsCredentials{
		AccessKeyID:     akid,
		SecretAccessKey: secret,
		SessionToken:    token,
	}, true
}

func loadAWSCredentialsFromSharedCredentialsFile() (awsCredentials, bool) {
	profile := strings.TrimSpace(os.Getenv("AWS_PROFILE"))
	if profile == "" {
		profile = strings.TrimSpace(os.Getenv("AWS_DEFAULT_PROFILE"))
	}
	if profile == "" {
		profile = "default"
	}

	path := strings.TrimSpace(os.Getenv("AWS_SHARED_CREDENTIALS_FILE"))
	if path == "" {
		home, err := os.UserHomeDir()
		if err != nil || strings.TrimSpace(home) == "" {
			return awsCredentials{}, false
		}
		path = home + "/.aws/credentials"
	}

	b, err := os.ReadFile(path) //nolint:gosec // user config path; contains secrets
	if err != nil {
		return awsCredentials{}, false
	}

	sections := parseINI(string(b))
	kv, ok := sections[profile]
	if !ok {
		return awsCredentials{}, false
	}

	akid := strings.TrimSpace(kv["aws_access_key_id"])
	secret := strings.TrimSpace(kv["aws_secret_access_key"])
	token := strings.TrimSpace(kv["aws_session_token"])

	if akid == "" || secret == "" {
		return awsCredentials{}, false
	}
	return awsCredentials{
		AccessKeyID:     akid,
		SecretAccessKey: secret,
		SessionToken:    token,
	}, true
}

func parseINI(s string) map[string]map[string]string {
	out := make(map[string]map[string]string)
	section := ""
	lines := strings.Split(s, "\n")
	for _, raw := range lines {
		line := strings.TrimSpace(raw)
		if line == "" {
			continue
		}
		// Comments
		if strings.HasPrefix(line, "#") || strings.HasPrefix(line, ";") {
			continue
		}
		// Section header
		if strings.HasPrefix(line, "[") && strings.HasSuffix(line, "]") {
			section = strings.TrimSpace(strings.TrimSuffix(strings.TrimPrefix(line, "["), "]"))
			if section != "" {
				if _, ok := out[section]; !ok {
					out[section] = make(map[string]string)
				}
			}
			continue
		}
		if section == "" {
			continue
		}
		// Key/value
		// Support `k = v` and ignore inline comments (`#` or `;`) when preceded by whitespace.
		k, v, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		k = strings.ToLower(strings.TrimSpace(k))
		v = strings.TrimSpace(v)
		if k == "" {
			continue
		}
		if i := strings.Index(v, " #"); i >= 0 {
			v = strings.TrimSpace(v[:i])
		}
		if i := strings.Index(v, " ;"); i >= 0 {
			v = strings.TrimSpace(v[:i])
		}
		out[section][k] = v
	}
	return out
}

// apiGatewayProxyTransport injects API Gateway auth and account routing headers,
// and signs requests with SigV4 (execute-api).
//
// Note: we intentionally disable HTTP compression in the base transport so the
// net/http transport does not add Accept-Encoding after signing.
type apiGatewayProxyTransport struct {
	Base    http.RoundTripper
	Region  string
	APIKey  string
	Account string
	Now     func() time.Time
}

func (t *apiGatewayProxyTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if req == nil {
		return nil, errNilRequest
	}
	if t.Base == nil {
		return nil, errNilBaseTransport
	}

	now := time.Now
	if t.Now != nil {
		now = t.Now
	}

	// Clone request to avoid mutating the input across retries.
	r := req.Clone(req.Context())
	if req.Body != nil {
		r.Body = req.Body
	}
	r.GetBody = req.GetBody

	// Required routing/auth headers for the proxy.
	if r.Header == nil {
		r.Header = make(http.Header)
	}
	r.Header.Set("x-api-key", t.APIKey)
	r.Header.Set("X-GOG-Account", strings.ToLower(strings.TrimSpace(t.Account)))

	amzTime := now().UTC()
	r.Header.Set("X-Amz-Date", amzTime.Format("20060102T150405Z"))

	creds, err := loadAWSCredentials()
	if err != nil {
		return nil, err
	}
	if creds.SessionToken != "" {
		r.Header.Set("X-Amz-Security-Token", creds.SessionToken)
	} else {
		r.Header.Del("X-Amz-Security-Token")
	}

	payloadHash, err := payloadSHA256Hex(r)
	if err != nil {
		return nil, err
	}

	authz, err := sigV4Authorization(sigV4Input{
		Request:     r,
		AccessKey:   creds.AccessKeyID,
		SecretKey:   creds.SecretAccessKey,
		Region:      t.Region,
		Service:     awsServiceExecuteAPI,
		SigningTime: amzTime,
		PayloadHash: payloadHash,
	})
	if err != nil {
		return nil, err
	}
	r.Header.Set("Authorization", authz)

	resp, err := t.Base.RoundTrip(r)
	if err != nil {
		return nil, fmt.Errorf("proxy roundtrip: %w", err)
	}

	return resp, nil
}

// payloadSHA256Hex returns the SHA256 of the request body without consuming it.
// It uses req.GetBody when available; otherwise it reads req.Body and rewinds it.
func payloadSHA256Hex(req *http.Request) (string, error) {
	if req == nil || req.Body == nil {
		sum := sha256.Sum256(nil)
		return hex.EncodeToString(sum[:]), nil
	}

	// Fast path: empty body.
	if req.ContentLength == 0 {
		sum := sha256.Sum256(nil)
		return hex.EncodeToString(sum[:]), nil
	}

	if req.GetBody != nil {
		rc, err := req.GetBody()
		if err != nil {
			return "", fmt.Errorf("get body for signing: %w", err)
		}
		defer rc.Close()
		h := sha256.New()
		if _, err := io.Copy(h, rc); err != nil {
			return "", fmt.Errorf("hash body for signing: %w", err)
		}
		return hex.EncodeToString(h.Sum(nil)), nil
	}

	// Fallback: read and rewind the body.
	b, err := io.ReadAll(req.Body)
	if err != nil {
		return "", fmt.Errorf("read body for signing: %w", err)
	}
	_ = req.Body.Close()
	req.Body = io.NopCloser(bytes.NewReader(b))
	req.GetBody = func() (io.ReadCloser, error) {
		return io.NopCloser(bytes.NewReader(b)), nil
	}
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:]), nil
}

type sigV4Input struct {
	Request     *http.Request
	AccessKey   string
	SecretKey   string
	Region      string
	Service     string
	SigningTime time.Time
	PayloadHash string
}

func sigV4Authorization(in sigV4Input) (string, error) {
	if in.Request == nil || in.Request.URL == nil {
		return "", errMissingRequestURL
	}
	if strings.TrimSpace(in.AccessKey) == "" || strings.TrimSpace(in.SecretKey) == "" {
		return "", errMissingAccessKeys
	}
	if strings.TrimSpace(in.Region) == "" || strings.TrimSpace(in.Service) == "" {
		return "", errMissingRegionService
	}
	if strings.TrimSpace(in.PayloadHash) == "" {
		return "", errMissingPayloadHash
	}

	amzDate := in.SigningTime.UTC().Format("20060102T150405Z")
	dateStamp := in.SigningTime.UTC().Format("20060102")

	canonicalURI := in.Request.URL.EscapedPath()
	if canonicalURI == "" {
		canonicalURI = "/"
	}

	canonicalQuery := canonicalQueryString(in.Request.URL)

	canonicalHeaders, signedHeaders := canonicalHeaders(in.Request)

	canonicalRequest := strings.Join([]string{
		in.Request.Method,
		canonicalURI,
		canonicalQuery,
		canonicalHeaders,
		signedHeaders,
		in.PayloadHash,
	}, "\n")

	scope := strings.Join([]string{dateStamp, in.Region, in.Service, "aws4_request"}, "/")
	stringToSign := strings.Join([]string{
		"AWS4-HMAC-SHA256",
		amzDate,
		scope,
		hexSHA256(canonicalRequest),
	}, "\n")

	signingKey := deriveSigningKey(in.SecretKey, dateStamp, in.Region, in.Service)
	signature := hex.EncodeToString(hmacSHA256(signingKey, stringToSign))

	return fmt.Sprintf(
		"AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s",
		in.AccessKey,
		scope,
		signedHeaders,
		signature,
	), nil
}

func canonicalQueryString(u *url.URL) string {
	if u == nil {
		return ""
	}
	q := u.Query()
	if len(q) == 0 {
		return ""
	}

	keys := make([]string, 0, len(q))
	for k := range q {
		keys = append(keys, k)
	}
	sort.Strings(keys)

	var parts []string
	for _, k := range keys {
		vals := q[k]
		sort.Strings(vals)
		ek := awsQueryEscape(k)
		for _, v := range vals {
			parts = append(parts, ek+"="+awsQueryEscape(v))
		}
	}
	return strings.Join(parts, "&")
}

func awsQueryEscape(s string) string {
	// url.QueryEscape is close but turns space into '+'; AWS expects '%20'.
	esc := url.QueryEscape(s)
	esc = strings.ReplaceAll(esc, "+", "%20")
	esc = strings.ReplaceAll(esc, "*", "%2A")
	esc = strings.ReplaceAll(esc, "%7E", "~")
	return esc
}

func canonicalHeaders(req *http.Request) (string, string) {
	// SigV4 requires Host and X-Amz-Date at minimum.
	headers := make(map[string][]string)

	host := ""
	if req != nil {
		if strings.TrimSpace(req.Host) != "" {
			host = strings.TrimSpace(req.Host)
		} else if req.URL != nil {
			host = strings.TrimSpace(req.URL.Host)
		}
	}
	if host != "" {
		headers["host"] = []string{host}
	}

	if req != nil {
		for k, vals := range req.Header {
			lk := strings.ToLower(strings.TrimSpace(k))
			if lk == "" {
				continue
			}
			// Skip headers that net/http may add after RoundTripper (we disable compression
			// in the base transport, but keep this as defense-in-depth).
			if lk == "accept-encoding" {
				continue
			}
			cleaned := make([]string, 0, len(vals))
			for _, v := range vals {
				v = strings.TrimSpace(v)
				if v == "" {
					continue
				}
				// Collapse sequential whitespace.
				v = strings.Join(strings.Fields(v), " ")
				cleaned = append(cleaned, v)
			}
			if len(cleaned) == 0 {
				continue
			}
			headers[lk] = append(headers[lk], cleaned...)
		}
	}

	names := make([]string, 0, len(headers))
	for k := range headers {
		names = append(names, k)
	}
	sort.Strings(names)

	var b strings.Builder
	signed := make([]string, 0, len(names))
	for _, name := range names {
		vals := headers[name]
		if len(vals) == 0 {
			continue
		}
		// Per AWS, multiple values are comma-separated.
		joined := strings.Join(vals, ",")
		b.WriteString(name)
		b.WriteByte(':')
		b.WriteString(joined)
		b.WriteByte('\n')
		signed = append(signed, name)
	}

	return b.String(), strings.Join(signed, ";")
}

func hexSHA256(s string) string {
	h := sha256.Sum256([]byte(s))
	return hex.EncodeToString(h[:])
}

func hmacSHA256(key []byte, data string) []byte {
	mac := hmac.New(sha256.New, key)
	_, _ = mac.Write([]byte(data))
	return mac.Sum(nil)
}

func deriveSigningKey(secretKey, dateStamp, region, service string) []byte {
	kDate := hmacSHA256([]byte("AWS4"+secretKey), dateStamp)
	kRegion := hmac.New(sha256.New, kDate)
	_, _ = kRegion.Write([]byte(region))
	kRegionSum := kRegion.Sum(nil)

	kService := hmac.New(sha256.New, kRegionSum)
	_, _ = kService.Write([]byte(service))
	kServiceSum := kService.Sum(nil)

	kSigning := hmac.New(sha256.New, kServiceSum)
	_, _ = kSigning.Write([]byte("aws4_request"))
	return kSigning.Sum(nil)
}
