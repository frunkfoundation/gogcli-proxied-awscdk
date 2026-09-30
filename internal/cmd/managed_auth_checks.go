package cmd

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	gapi "google.golang.org/api/googleapi"

	"github.com/openclaw/gogcli/internal/googleapi"
	"github.com/openclaw/gogcli/internal/outfmt"
)

var errManagedIdentityMismatch = errors.New("proxy returned a different Google account identity")

type managedCallerCheck struct {
	Service string `json:"service"`
	Status  string `json:"status"`
	Detail  string `json:"detail"`
}

type ManagedAuthDoctorCmd struct {
	Check    bool          `name:"check" help:"Perform minimal service reads as this caller through the proxy"`
	Services string        `name:"services" help:"Services to check: gmail,drive (default: account's enrolled supported services)"`
	Timeout  time.Duration `name:"timeout" help:"Timeout for inventory and each service check" default:"15s"`
}

func (c *ManagedAuthDoctorCmd) Run(ctx context.Context, flags *RootFlags) error {
	if c.Timeout <= 0 {
		return usage("timeout must be positive")
	}
	names, parseErr := requestedCheckServices(c.Services)
	if parseErr != nil {
		return parseErr
	}
	if len(names) > 0 && !c.Check {
		return usage("--services requires --check")
	}
	var checks []authDoctorCheck
	add := func(name, status, detail string) {
		checks = append(checks, authDoctorCheck{Name: name, Status: status, Detail: detail})
	}
	store, err := commandConfigStore(ctx)
	if err == nil {
		_, err = store.Read()
	}
	if err != nil {
		add("config", doctorError, "configuration could not be read")
	} else {
		add("config", doctorOK, store.Path())
	}
	bounded, cancel := context.WithTimeout(ctx, c.Timeout)
	defer cancel()
	if err == nil {
		info, connectionErr := googleapi.ProxyConnection(bounded)
		err = connectionErr
		if err != nil {
			add("proxy.credentials", doctorError, err.Error())
		} else {
			add("proxy.credentials", doctorOK, "AWS profile="+info.AWSProfile+" source="+info.CredentialSource)
		}
	}
	var inventory googleapi.AccountInventory
	if err == nil {
		inventory, err = googleapi.ListManagedAccounts(bounded)
		if err != nil {
			add("proxy.inventory", doctorError, err.Error())
		} else {
			add("proxy.inventory", doctorOK, "signed caller inventory is accessible")
		}
	}
	if err == nil {
		var account string
		account, err = selectManagedAccount(flags, inventory)
		if err != nil {
			add("account.selection", doctorError, err.Error())
		} else {
			add("account.selection", doctorOK, account)
			for _, candidate := range inventory.Accounts {
				if candidate.Email != account {
					continue
				}
				connectionStatus := doctorOK
				if candidate.ConnectionStatus != "connected" {
					connectionStatus = doctorWarn
				}
				add("account.connection", connectionStatus, candidate.ConnectionStatus+" (recorded; no live caller check)")
				for _, service := range candidate.Services {
					status := doctorOK
					if service.AuthorizationStatus != "granted" || service.LastVerificationStatus != "succeeded" {
						status = doctorWarn
					}
					add("recorded."+service.Name, status, fmt.Sprintf("authorization=%s last_verification=%s verified_at=%d", service.AuthorizationStatus, service.LastVerificationStatus, service.LastVerifiedAt))
				}
				if c.Check {
					live := managedChecks(ctx, candidate, names, c.Timeout)
					for _, check := range live {
						add("caller."+check.Service, check.Status, check.Detail)
					}
				}
			}
		}
	}
	status := authDoctorStatus(checks)
	if outfmt.IsJSON(ctx) {
		err = outfmt.WriteJSON(ctx, stdoutWriter(ctx), map[string]any{"status": status, "checks": checks})
	} else {
		for _, check := range checks {
			fmt.Fprintf(stdoutWriter(ctx), "%s\t%s\t%s\n", check.Status, check.Name, check.Detail)
		}
		fmt.Fprintf(stdoutWriter(ctx), "status\t%s\n", status)
	}
	if err != nil {
		return err
	}
	if status == doctorError {
		return fmt.Errorf("managed account diagnostics failed; see reported checks")
	}
	return nil
}

func requestedCheckServices(csv string) ([]string, error) {
	if strings.TrimSpace(csv) == "" {
		return nil, nil
	}
	names := []string{}
	for _, name := range strings.Split(csv, ",") {
		name = strings.ToLower(strings.TrimSpace(name))
		if name != "gmail" && name != "drive" {
			return nil, usage("service checks support gmail and drive")
		}
		names = append(names, name)
	}
	return names, nil
}

func managedChecks(ctx context.Context, account googleapi.ManagedAccount, names []string, timeout time.Duration) []managedCallerCheck {
	if names == nil {
		for _, service := range account.Services {
			if service.Name == "gmail" || service.Name == "drive" {
				names = append(names, service.Name)
			}
		}
	}
	checks := make([]managedCallerCheck, 0, len(names))
	for _, name := range names {
		granted := false
		for _, service := range account.Services {
			if service.Name == name && service.AuthorizationStatus == "granted" {
				granted = true
			}
		}
		check := managedCallerCheck{Service: name, Status: doctorError, Detail: "service authorization is not recorded as granted; review the owner page"}
		if granted {
			bounded, cancel := context.WithTimeout(ctx, timeout)
			err := managedServiceProbe(bounded, account.Email, name)
			cancel()
			if err == nil {
				check.Status, check.Detail = doctorOK, "minimal reads succeeded as this caller"
			} else {
				check.Detail = safeManagedProbeError(err)
			}
		}
		checks = append(checks, check)
	}
	if len(checks) == 0 {
		checks = append(checks, managedCallerCheck{Status: doctorWarn, Detail: "no enrolled Gmail or Drive services to check"})
	}
	return checks
}

func managedServiceProbe(ctx context.Context, email, name string) error {
	switch name {
	case "gmail":
		service, err := googleapi.NewGmail(ctx, email)
		if err != nil {
			return err
		}
		profile, err := service.Users.GetProfile("me").Fields(gapi.Field("emailAddress")).Context(ctx).Do()
		if err != nil {
			return err
		}
		if profile == nil || !strings.EqualFold(strings.TrimSpace(profile.EmailAddress), strings.TrimSpace(email)) {
			return errManagedIdentityMismatch
		}
		_, err = service.Users.Messages.List("me").MaxResults(1).Fields(gapi.Field("messages(id)")).Context(ctx).Do()
		return err
	case "drive":
		service, err := googleapi.NewDrive(ctx, email)
		if err != nil {
			return err
		}
		about, err := service.About.Get().Fields(gapi.Field("user(emailAddress)")).Context(ctx).Do()
		if err != nil {
			return err
		}
		if about == nil || about.User == nil || !strings.EqualFold(strings.TrimSpace(about.User.EmailAddress), strings.TrimSpace(email)) {
			return errManagedIdentityMismatch
		}
		_, err = service.Files.List().PageSize(1).Fields(gapi.Field("files(id)")).Context(ctx).Do()
		return err
	default:
		return usage("service checks support gmail and drive")
	}
}

func safeManagedProbeError(err error) string {
	// Google error bodies can contain request content. Report only status/type.
	if errors.Is(err, errManagedIdentityMismatch) {
		return "proxy returned an unexpected Google account identity; review the remote connection"
	}
	var apiErr *gapi.Error
	if errors.As(err, &apiErr) {
		if apiErr.Code == 401 || apiErr.Code == 403 {
			return fmt.Sprintf("caller service read denied (HTTP %d); review caller access and service authorization", apiErr.Code)
		}
		return fmt.Sprintf("caller service read failed (HTTP %d)", apiErr.Code)
	}
	if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) {
		return "caller service read interrupted"
	}
	return "caller service read failed; check proxy configuration, AWS credentials, and remote service status"
}

func callerChecksFailed(checks []managedCallerCheck) bool {
	for _, check := range checks {
		if check.Status == doctorError {
			return true
		}
	}
	return false
}
