package googleapi

import (
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

const proxyDownloadMediaType = "application/vnd.gog.download+json"

var (
	errProxyDownload      = errors.New("invalid proxy download handoff")
	errProxyDownloadFetch = errors.New("proxy download storage request failed")
	driveDownloadPath     = regexp.MustCompile(`^/drive/v3/files/[^/]+(?:/export)?$`)
	storageDownloadHost   = regexp.MustCompile(`^[a-z0-9][a-z0-9.-]*\.s3\.[a-z]{2}(?:-[a-z]+)+-[0-9]+\.amazonaws\.com$`)
)

func eligibleProxyDownload(req *http.Request) bool {
	return req.Method == http.MethodGet && req.URL.Hostname() == "www.googleapis.com" && req.Header.Get("Range") == "" &&
		driveDownloadPath.MatchString(req.URL.Path) && (strings.HasSuffix(req.URL.Path, "/export") || req.URL.Query().Get("alt") == "media")
}

func setProxyDownloadMode(req, original *http.Request) {
	req.Header.Del("X-GOG-Response-Mode")

	if eligibleProxyDownload(original) {
		req.Header.Set("X-GOG-Response-Mode", "signed-url")
	}
}

func (t *apiGatewayProxyTransport) downloadResponse(req *http.Request, resp *http.Response) (*http.Response, error) {
	mediaType, _, _ := mime.ParseMediaType(resp.Header.Get("Content-Type"))
	if mediaType != proxyDownloadMediaType {
		return resp, nil
	}
	defer resp.Body.Close()

	if !eligibleProxyDownload(req) || resp.StatusCode != http.StatusOK {
		return nil, errProxyDownload
	}
	var envelope struct {
		URL     string            `json:"url"`
		Status  int               `json:"status"`
		Headers map[string]string `json:"headers"`
		Size    int64             `json:"size"`
	}

	data, err := io.ReadAll(io.LimitReader(resp.Body, (64<<10)+1))
	if err != nil || len(data) > 64<<10 || json.Unmarshal(data, &envelope) != nil || envelope.Status != 200 || envelope.Size < 0 {
		return nil, errProxyDownload
	}

	target, err := url.Parse(envelope.URL)
	if err != nil || target.Scheme != "https" || target.User != nil || target.Port() != "" || target.Fragment != "" || !storageDownloadHost.MatchString(target.Host) || target.Path == "" || target.Query().Get("X-Amz-Algorithm") != "AWS4-HMAC-SHA256" || target.Query().Get("X-Amz-Signature") == "" {
		return nil, errProxyDownload
	}

	download, err := http.NewRequestWithContext(req.Context(), http.MethodGet, target.String(), nil)
	if err != nil {
		return nil, errProxyDownload
	}

	result, err := t.Base.RoundTrip(download)
	if err != nil {
		return nil, errProxyDownloadFetch
	}

	if result.StatusCode != http.StatusOK {
		_ = result.Body.Close()
		return nil, errProxyDownloadFetch
	}

	result.Header = make(http.Header, len(envelope.Headers)+1)
	for name, value := range envelope.Headers {
		result.Header.Set(name, value)
	}

	result.Header.Set("Content-Length", strconv.FormatInt(envelope.Size, 10))
	result.ContentLength = envelope.Size
	result.StatusCode = envelope.Status
	result.Status = "200 OK"
	result.Request = req
	result.Body = &downloadSizeBody{ReadCloser: result.Body, remaining: envelope.Size}

	return result, nil
}

// Check the staged byte count even when a custom transport omits Content-Length.
type downloadSizeBody struct {
	io.ReadCloser
	remaining int64
}

func (b *downloadSizeBody) Read(p []byte) (int, error) {
	n, err := b.ReadCloser.Read(p)

	b.remaining -= int64(n)
	if b.remaining < 0 {
		return n, errProxyDownloadFetch
	}

	if errors.Is(err, io.EOF) && b.remaining != 0 {
		return n, io.ErrUnexpectedEOF
	}

	return n, err //nolint:wrapcheck // Reader callers need the underlying EOF and cancellation errors.
}
