package cmd

import "strings"

func splitCSV(s string) []string {
	s = strings.TrimSpace(s)
	if s == "" {
		return nil
	}
	parts := strings.Split(s, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}

// Keep repeated flags raw with sep:"none"; splitCSV owns comma splitting
// so a backslash before a comma remains literal.
func splitCSVAllRaw(raws []string) []string {
	out := make([]string, 0, len(raws))
	for _, raw := range raws {
		out = append(out, splitCSV(raw)...)
	}
	return out
}
