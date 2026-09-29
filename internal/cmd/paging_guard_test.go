package cmd

import (
	"strconv"
	"strings"
	"testing"
)

func TestPageTokenGuard(t *testing.T) {
	for _, tokens := range [][]string{{"", ""}, {"start", "start"}, {"a", "b", "c", "a"}} {
		var guard pageTokenGuard
		for i, token := range tokens {
			err := guard.check(token)
			if i < len(tokens)-1 {
				if err != nil {
					t.Fatal(err)
				}
			} else if err == nil || !strings.Contains(err.Error(), "repeated page token") {
				t.Fatalf("tokens %q: expected cycle error, got %v", tokens, err)
			}
		}
	}
}

func TestPageTokenGuardPreservesUnboundedOpaqueTokens(t *testing.T) {
	var guard pageTokenGuard
	for i := range 10_001 {
		if err := guard.check(strconv.Itoa(i)); err != nil {
			t.Fatal(err)
		}
	}
	for _, token := range []string{"", " ", "0 "} {
		if err := guard.check(token); err != nil {
			t.Fatalf("distinct opaque token %q: %v", token, err)
		}
	}
}

func TestCollectUnboundedPagesHasNoPageCeiling(t *testing.T) {
	calls := 0
	rows, err := collectUnboundedPages("", func(token string) ([]int, string, error) {
		calls++
		next := strconv.Itoa(calls)
		if calls == 10_001 {
			next = ""
		}
		return []int{calls}, next, nil
	})
	if err != nil || len(rows) != 10_001 {
		t.Fatalf("got %d rows after %d calls: %v", len(rows), calls, err)
	}
}
