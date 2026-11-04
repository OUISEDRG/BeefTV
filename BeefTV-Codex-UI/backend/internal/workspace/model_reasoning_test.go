package workspace

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"infinite-canvas/backend/internal/outbound"
)

type reasoningFixtureConfig []byte

func (f reasoningFixtureConfig) ReadLocalModelConfig() ([]byte, error) { return []byte(f), nil }

func reasoningFixtureService(t *testing.T, baseURL string, change func(map[string]any)) *ModelReasoningService {
	t.Helper()
	channel := map[string]any{"id": "local-test", "enabled": true, "baseUrl": baseURL + "/v1", "apiKey": "fixture-local-secret", "apiFormat": "openai", "models": []string{"model-a", "model-b"}, "modelProfiles": []any{
		map[string]any{"model": "model-a", "capability": "text", "protocol": "chat-completion"},
		map[string]any{"model": "model-b", "capability": "image", "protocol": "openai-image"},
	}}
	if change != nil {
		change(channel)
	}
	body, err := json.Marshal(map[string]any{"channels": []any{channel}})
	if err != nil {
		t.Fatal(err)
	}
	return NewModelReasoningService(reasoningFixtureConfig(body))
}

func reasoningSnapshot() string {
	return `{"models":[{"id":"model-a","model":"model-a","displayName":"Local Model A","defaultReasoningEffort":"medium","supportedReasoningEfforts":[{"reasoningEffort":"medium","description":"Balanced"},{"reasoningEffort":"high","description":"Deep"}],"apiKey":"fixture-local-secret"},{"id":"not-configured","model":"not-configured","displayName":"Other","defaultReasoningEffort":"medium","supportedReasoningEfforts":[{"reasoningEffort":"medium","description":"Balanced"}]}],"overrides":{"model-a":"high","not-configured":"medium"},"revision":3,"apiKey":"fixture-local-secret"}`
}

func TestModelReasoningUsesStoredCredentialAndProjectsSettings(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || r.URL.Path != "/v1/reasoning-settings" || r.URL.RawQuery != "" {
			t.Errorf("unexpected request %s %s", r.Method, r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer fixture-local-secret" {
			t.Error("stored credential was not sent")
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(reasoningSnapshot()))
	}))
	defer server.Close()
	settings, err := reasoningFixtureService(t, server.URL, nil).ReadModelReasoning(context.Background(), "local-test")
	if err != nil {
		t.Fatal(err)
	}
	if settings.ChannelID != "local-test" || settings.Revision != 3 || len(settings.Models) != 1 || settings.Models[0].ID != "model-a" || settings.Overrides["model-a"] != "high" {
		t.Fatalf("wrong snapshot: %#v", settings)
	}
	raw, _ := json.Marshal(settings)
	if strings.Contains(string(raw), "fixture-local-secret") || strings.Contains(string(raw), "not-configured") {
		t.Fatal("response leaked credentials or unconfigured models")
	}
}

func TestModelReasoningPutPassesCASAndNullResetOnly(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		if json.NewDecoder(r.Body).Decode(&body) != nil {
			t.Error("invalid request body")
		}
		if r.Method != http.MethodPut || len(body) != 3 || body["model"] != "model-a" || body["effort"] != nil || body["expectedRevision"] != float64(3) {
			t.Errorf("unexpected body %#v", body)
		}
		_, _ = w.Write([]byte(reasoningSnapshot()))
	}))
	defer server.Close()
	_, err := reasoningFixtureService(t, server.URL, nil).UpdateModelReasoning(context.Background(), ModelReasoningUpdate{ChannelID: "local-test", Model: "model-a", ExpectedRevision: 3})
	if err != nil {
		t.Fatal(err)
	}
}

func TestModelReasoningRejectsUnsafeOrNontextChannelsBeforeRequest(t *testing.T) {
	for name, change := range map[string]func(map[string]any){
		"remote":   func(c map[string]any) { c["baseUrl"] = "https://example.invalid/v1" },
		"userinfo": func(c map[string]any) { c["baseUrl"] = "http://user:secret@127.0.0.1:1/v1" },
		"query":    func(c map[string]any) { c["baseUrl"] = "http://127.0.0.1:1/v1?token=secret" },
		"disabled": func(c map[string]any) { c["enabled"] = false },
		"managed":  func(c map[string]any) { c["credentialRef"] = "managed" },
		"wrong-protocol": func(c map[string]any) {
			c["modelProfiles"] = []any{map[string]any{"model": "model-a", "capability": "text", "protocol": "responses"}}
		},
		"image": func(c map[string]any) {
			c["modelProfiles"] = []any{map[string]any{"model": "model-a", "capability": "image", "protocol": "chat-completion"}}
		},
	} {
		t.Run(name, func(t *testing.T) {
			_, err := reasoningFixtureService(t, "http://127.0.0.1:1", change).ReadModelReasoning(context.Background(), "local-test")
			var public *ModelReasoningError
			if !errors.As(err, &public) || public.Reason != "reasoning_unsupported" {
				t.Fatalf("unexpected rejection: %v", err)
			}
		})
	}
}

func TestModelReasoningErrorProjectionPreservesCASReasonWithoutSecrets(t *testing.T) {
	for _, tc := range []struct {
		status       int
		body, reason string
	}{
		{409, `{"error":{"message":"fixture-local-secret","code":"settings_conflict"}}`, "settings_conflict"},
		{400, `{"error":{"message":"fixture-local-secret","code":"unsupported_reasoning"}}`, "unsupported_reasoning"},
		{404, `fixture-local-secret`, "reasoning_unsupported"},
		{401, `{"error":{"message":"fixture-local-secret"}}`, "reasoning_auth_failed"},
		{302, `fixture-local-secret`, "reasoning_upstream_error"},
	} {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(tc.status)
			_, _ = w.Write([]byte(tc.body))
		}))
		_, err := reasoningFixtureService(t, server.URL, nil).ReadModelReasoning(context.Background(), "local-test")
		server.Close()
		var public *ModelReasoningError
		if !errors.As(err, &public) || public.Reason != tc.reason || strings.Contains(public.Message, "fixture-local-secret") {
			t.Fatalf("unsafe error projection: %#v", err)
		}
	}
}

func TestModelReasoningRejectsSecretInPublicField(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(strings.Replace(reasoningSnapshot(), "Local Model A", "fixture-local-secret", 1)))
	}))
	defer server.Close()
	_, err := reasoningFixtureService(t, server.URL, nil).ReadModelReasoning(context.Background(), "local-test")
	var public *ModelReasoningError
	if !errors.As(err, &public) || public.Reason != "reasoning_invalid_response" {
		t.Fatalf("secret in public field accepted: %v", err)
	}
}

func TestModelReasoningTimeoutAndCancellation(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { <-r.Context().Done() }))
	defer server.Close()
	service := reasoningFixtureService(t, server.URL, nil)
	service.client = outbound.LoopbackHTTPClient(25 * time.Millisecond)
	_, err := service.ReadModelReasoning(context.Background(), "local-test")
	var public *ModelReasoningError
	if !errors.As(err, &public) || public.Reason != "reasoning_timeout" {
		t.Fatalf("timeout not preserved: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err = service.ReadModelReasoning(ctx, "local-test")
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("cancellation not preserved: %v", err)
	}
}

func TestModelReasoningPreservesCancellationWhileReadingBody(t *testing.T) {
	started := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		w.(http.Flusher).Flush()
		close(started)
		<-r.Context().Done()
	}))
	defer server.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	result := make(chan error, 1)
	go func() {
		_, err := reasoningFixtureService(t, server.URL, nil).ReadModelReasoning(ctx, "local-test")
		result <- err
	}()
	<-started
	cancel()
	if err := <-result; !errors.Is(err, context.Canceled) {
		t.Fatalf("body cancellation not preserved: %v", err)
	}
}
