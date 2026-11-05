package cmd

import "strings"

type AuthCmd struct {
	Setup    ManagedAuthSetupCmd  `cmd:"" name:"setup" help:"Show the owner page for Google account setup"`
	Add      ManagedAuthAddCmd    `cmd:"" name:"add" help:"Show where to connect a managed Google account"`
	Services AuthServicesCmd      `cmd:"" name:"services" help:"Show upstream services and managed enrollment limits"`
	List     ManagedAuthListCmd   `cmd:"" name:"list" help:"List this caller's visible remote account connections"`
	Doctor   ManagedAuthDoctorCmd `cmd:"" name:"doctor" help:"Diagnose proxy credentials, remote accounts, and caller reads"`
	Aliases  AuthAliasCmd         `cmd:"" name:"alias" help:"Manage local account aliases"`
	Status   ManagedAuthStatusCmd `cmd:"" name:"status" help:"Show proxy configuration and remote inventory access"`
	Remove   ManagedAuthRemoveCmd `cmd:"" name:"remove" help:"Show where to remove remote account access"`
	Manage   ManagedAuthManageCmd `cmd:"" name:"manage" help:"Show the owner account management page" aliases:"login"`
}

func splitCommaList(raw string) []string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil
	}
	out := make([]string, 0)
	fields := strings.FieldsFunc(raw, func(r rune) bool {
		return r == ',' || r == '\n' || r == '\t' || r == ' '
	})
	for _, f := range fields {
		f = strings.TrimSpace(f)
		if f == "" {
			continue
		}
		out = append(out, f)
	}
	return out
}
