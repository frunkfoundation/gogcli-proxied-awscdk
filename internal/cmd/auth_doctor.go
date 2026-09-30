package cmd

type authDoctorCheck struct {
	Name   string `json:"name"`
	Status string `json:"status"`
	Detail string `json:"detail,omitempty"`
	Hint   string `json:"hint,omitempty"`
}

const (
	doctorOK    = "ok"
	doctorWarn  = "warn"
	doctorError = literalError
)

func authDoctorStatus(checks []authDoctorCheck) string {
	status := doctorOK
	for _, check := range checks {
		switch check.Status {
		case doctorError:
			return doctorError
		case doctorWarn:
			status = doctorWarn
		}
	}
	return status
}
