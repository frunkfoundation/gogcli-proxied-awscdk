package googleapi

import (
	"context"
	"testing"
)

func TestFactoryBuildsRepresentativeServices(t *testing.T) {
	t.Parallel()

	ctx := proxyFixtureContext(context.Background(), "https://abc.execute-api.us-east-1.amazonaws.com/prod")
	deps, _ := authDependenciesFromContext(ctx)
	factory := NewFactory(deps, FactoryOptions{
		PhotosBaseURL:       "https://photos.example.test/v1",
		PhotosPickerBaseURL: "https://picker.example.test/v1",
	})

	if svc, err := factory.Drive(ctx, "user@example.com"); err != nil || svc == nil {
		t.Fatalf("Drive() = (%v, %v)", svc, err)
	}

	if svc, err := factory.Gmail(ctx, "user@example.com"); err != nil || svc == nil {
		t.Fatalf("Gmail() = (%v, %v)", svc, err)
	}

	if svc, err := factory.Docs(ctx, "user@example.com"); err != nil || svc == nil {
		t.Fatalf("Docs() = (%v, %v)", svc, err)
	}

	if svc, err := factory.AdSense(ctx, "user@example.com"); err != nil || svc == nil {
		t.Fatalf("AdSense() = (%v, %v)", svc, err)
	}

	if svc, err := factory.Calendar(ctx, "user@example.com"); err != nil || svc == nil {
		t.Fatalf("Calendar() = (%v, %v)", svc, err)
	}

	if svc, err := factory.ChatSearch(ctx, "user@example.com"); err != nil || svc == nil {
		t.Fatalf("ChatSearch() = (%v, %v)", svc, err)
	}

	if svc, err := factory.Sheets(ctx, "user@example.com"); err != nil || svc == nil {
		t.Fatalf("Sheets() = (%v, %v)", svc, err)
	}

	if svc, err := factory.YouTubeAccount(ctx, "user@example.com"); err != nil || svc == nil {
		t.Fatalf("YouTubeAccount() = (%v, %v)", svc, err)
	}

	photos, err := factory.Photos(ctx, "user@example.com")
	if err != nil {
		t.Fatalf("Photos(): %v", err)
	}

	if photos.baseURL != "https://photos.example.test/v1" {
		t.Fatalf("Photos base URL = %q", photos.baseURL)
	}

	picker, err := factory.PhotosPicker(ctx, "user@example.com")
	if err != nil {
		t.Fatalf("PhotosPicker(): %v", err)
	}

	if picker.baseURL != "https://picker.example.test/v1" {
		t.Fatalf("Photos Picker base URL = %q", picker.baseURL)
	}
}
