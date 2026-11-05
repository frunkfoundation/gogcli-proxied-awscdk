package googleapi

import (
	"context"

	"google.golang.org/api/keep/v1"

	"github.com/openclaw/gogcli/internal/googleauth"
)

func NewKeep(ctx context.Context, email string) (*keep.Service, error) {
	return newGoogleServiceForAccount(ctx, email, googleauth.ServiceKeep, "keep", keep.NewService)
}
