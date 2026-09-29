package cmd

import (
	"encoding/json"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
)

func backupPagingTestHandler(t *testing.T, calls *atomic.Int32, pathSuffix string, pages []map[string]any, query map[string]string) http.HandlerFunc {
	t.Helper()
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || !strings.HasSuffix(r.URL.Path, pathSuffix) {
			http.NotFound(w, r)
			return
		}
		n := int(calls.Add(1))
		if n > len(pages) {
			http.Error(w, "unexpected extra page request", http.StatusBadRequest)
			return
		}
		previousToken := ""
		if n > 1 {
			previousToken, _ = pages[n-2]["nextPageToken"].(string)
		}
		requireQuery(t, r, "pageToken", previousToken)
		for name, value := range query {
			requireQuery(t, r, name, value)
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(pages[n-1])
	}
}
