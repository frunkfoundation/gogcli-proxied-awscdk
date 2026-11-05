package googleapi

import (
	"context"

	"github.com/openclaw/gogcli/internal/config"
)

type AuthMode string

const (
	AuthModeStored AuthMode = ""
	AuthModeADC    AuthMode = "adc"
)

type AuthDependencies struct {
	ReadConfig func() (config.File, error)
	Mode       AuthMode
}
type authDependenciesContextKey struct{}

func ParseAuthMode(value string) AuthMode {
	if value == string(AuthModeADC) {
		return AuthModeADC
	}

	return AuthModeStored
}

func WithAuthDependencies(ctx context.Context, dependencies AuthDependencies) context.Context {
	return context.WithValue(ctx, authDependenciesContextKey{}, dependencies)
}

func authDependenciesFromContext(ctx context.Context) (AuthDependencies, bool) {
	if ctx == nil {
		return AuthDependencies{}, false
	}
	deps, ok := ctx.Value(authDependenciesContextKey{}).(AuthDependencies)

	return deps, ok
}
