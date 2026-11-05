package googleapi

import (
	"context"
	"testing"
)

func TestNewTasks(t *testing.T) {
	ctx := proxyFixtureContext(context.Background(), "https://abc.execute-api.us-east-1.amazonaws.com/prod")

	svc, err := NewTasks(ctx, "a@b.com")
	if err != nil || svc == nil {
		t.Fatalf("service=%v error=%v", svc, err)
	}
}
