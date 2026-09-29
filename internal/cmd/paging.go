package cmd

import (
	"fmt"
	"strings"
)

const emptyResultsExitCode = 3

func failEmptyExit(failEmpty bool) error {
	if !failEmpty {
		return nil
	}
	return &ExitError{Code: emptyResultsExitCode, Err: nil}
}

// collectAllPages keeps calling fetch until it returns an empty next page token.
// It guards against pagination loops by tracking seen page tokens.
func collectAllPages[T any](startPageToken string, fetch func(pageToken string) ([]T, string, error)) ([]T, error) {
	return collectPages(startPageToken, 10_000, fetch)
}

// collectUnboundedPages preserves the capacity of existing unbounded listings.
func collectUnboundedPages[T any](startPageToken string, fetch func(pageToken string) ([]T, string, error)) ([]T, error) {
	return collectPages(startPageToken, 0, fetch)
}

func collectPages[T any](startPageToken string, maxPages int, fetch func(pageToken string) ([]T, string, error)) ([]T, error) {
	pageToken := strings.TrimSpace(startPageToken)
	var guard pageTokenGuard

	var out []T
	for i := 0; maxPages == 0 || i < maxPages; i++ {
		if err := guard.check(pageToken); err != nil {
			return nil, err
		}

		items, next, err := fetch(pageToken)
		if err != nil {
			return nil, err
		}
		out = append(out, items...)

		next = strings.TrimSpace(next)
		if next == "" {
			return out, nil
		}
		pageToken = next
	}
	return nil, fmt.Errorf("pagination exceeded max pages")
}
