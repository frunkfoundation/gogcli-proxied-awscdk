package cmd

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	youtube "google.golang.org/api/youtube/v3"

	"github.com/openclaw/gogcli/internal/app"
)

func TestYouTubePlaylistsListHonorsCanceledContext(t *testing.T) {
	ctx, cancel := context.WithCancel(newCmdRuntimeOutputContext(t, io.Discard, io.Discard))
	defer cancel()
	var requests atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		cancel()
		select {
		case <-r.Context().Done():
		case <-time.After(2 * time.Second):
			http.Error(w, "request ignored cancellation", http.StatusGatewayTimeout)
		}
	}))
	t.Cleanup(srv.Close)

	svc := newGoogleTestServiceWithEndpoint(t, srv.Client(), srv.URL+"/", youtube.NewService)
	ctx = withYouTubeTestServices(ctx, youtubeTestServices{
		Account: fixedYouTubeTestService(svc),
	})

	err := runKong(t, &YouTubePlaylistsListCmd{}, []string{"--mine"}, ctx, &RootFlags{
		Account: "me@example.com",
	})
	if requests.Load() != 1 {
		t.Fatalf("request count = %d, want cancellation after dispatch", requests.Load())
	}
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("err = %v, want context.Canceled", err)
	}
}

func TestChatMessagesListUnreadHonorsCanceledContext(t *testing.T) {
	ctx, cancel := context.WithCancel(newCmdRuntimeOutputContext(t, io.Discard, io.Discard))
	defer cancel()
	var requests atomic.Int32
	svc := useFakeChatService(t, func(w http.ResponseWriter, r *http.Request) {
		if !strings.Contains(r.URL.Path, "spaceReadState") {
			http.NotFound(w, r)
			return
		}
		requests.Add(1)
		cancel()
		select {
		case <-r.Context().Done():
		case <-time.After(2 * time.Second):
			http.Error(w, "request ignored cancellation", http.StatusGatewayTimeout)
		}
	})
	ctx = withTestRuntime(ctx, func(runtime *app.Runtime) {
		runtime.Services.Chat = chatTestServices.fixed(svc)
	})

	err := runKong(t, &ChatMessagesListCmd{}, []string{"spaces/aaa", "--unread"}, ctx, &RootFlags{
		Account: "a@b.com",
	})
	if requests.Load() != 1 {
		t.Fatalf("request count = %d, want cancellation after dispatch", requests.Load())
	}
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("err = %v, want context.Canceled", err)
	}
}

func TestPeopleProfileGetHonorsCanceledContext(t *testing.T) {
	ctx, cancel := context.WithCancel(newCmdRuntimeOutputContext(t, io.Discard, io.Discard))
	defer cancel()
	var requests atomic.Int32
	svc, closeSrv := newPeopleService(t, func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		cancel()
		select {
		case <-r.Context().Done():
		case <-time.After(2 * time.Second):
			http.Error(w, "request ignored cancellation", http.StatusGatewayTimeout)
		}
	})
	t.Cleanup(closeSrv)

	ctx = withPeopleTestServices(ctx, peopleTestServices{
		Directory: fixedPeopleTestService(svc),
	})

	err := runKong(t, &PeopleGetCmd{}, []string{"people/123"}, ctx, &RootFlags{
		Account: "a@b.com",
	})
	if requests.Load() != 1 {
		t.Fatalf("request count = %d, want cancellation after dispatch", requests.Load())
	}
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("err = %v, want context.Canceled", err)
	}
}
