package cmd

import "fmt"

// pageTokenGuard detects cycles without changing a caller's page limit or
// partial-result and early-return behavior. Tokens remain opaque.
type pageTokenGuard struct {
	seen map[string]struct{}
}

func (g *pageTokenGuard) check(token string) error {
	if _, exists := g.seen[token]; exists {
		return fmt.Errorf("pagination loop: repeated page token %q", token)
	}
	if g.seen == nil {
		g.seen = make(map[string]struct{})
	}
	g.seen[token] = struct{}{}
	return nil
}
