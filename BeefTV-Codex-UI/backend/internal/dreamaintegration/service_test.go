package dreamaintegration

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

type fixtureConfig struct {
	body     []byte
	revision int64
	writes   int
}

func (f *fixtureConfig) ReadLocalModelConfig() ([]byte, error) { return f.body, nil }
func (f *fixtureConfig) SaveLocalModelConfigRevision(body []byte, revision int64) (int64, error) {
	if revision != f.revision {
		return 0, errors.New("revision conflict")
	}
	f.body = body
	f.revision++
	f.writes++
	return f.revision, nil
}
func fixture(t *testing.T, endpoint string) *fixtureConfig {
	t.Helper()
	body, _ := json.Marshal(map[string]any{"videoModel": "dreama-local-preflight::old", "assistantModel": "codex::gpt", "channels": []any{
		map[string]any{"id": "dreama-local-preflight", "enabled": true, "scope": "user", "baseUrl": endpoint + "/v1", "apiKey": "fixture-secret", "models": []string{"old"}, "modelProfiles": []any{map[string]any{"model": "old", "protocol": "dreama-videos-async", "capability": "video"}}},
		map[string]any{"id": "codex", "enabled": true, "baseUrl": "http://127.0.0.1:18766/v1", "apiKey": "keep-codex", "models": []string{"gpt"}},
	}})
	return &fixtureConfig{body: body, revision: 4}
}
func TestExchangeUsesSavedCredentialAndFixedIntegrationPath(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/integration/v1/series" || r.URL.Query().Get("projectId") != "12" || r.Header.Get("Authorization") != "Bearer fixture-secret" {
			t.Error("incorrect request contract")
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"items":[{"id":3,"projectId":12,"name":"第一集"}]}`))
	}))
	defer server.Close()
	svc := New(fixture(t, server.URL))
	body, err := svc.Exchange(context.Background(), "dreama-local-preflight", "GET", "series", map[string]string{"projectId": "12"}, nil)
	if err != nil || !strings.Contains(string(body), "第一集") {
		t.Fatalf("exchange: %s %v", body, err)
	}
}
func TestExchangeRejectsUntrustedRouteAndChannelWithoutSending(t *testing.T) {
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls++ }))
	defer server.Close()
	svc := New(fixture(t, server.URL))
	for _, request := range []struct {
		channel, method, path string
		query                 map[string]string
	}{
		{"codex", "GET", "status", nil}, {"dreama-local-preflight", "GET", "../v1/jobs", nil},
		{"dreama-local-preflight", "GET", "projects", map[string]string{"apiKey": "bad"}},
		{"dreama-local-preflight", "POST", "tasks/a/approve", nil},
		{"dreama-local-preflight", "POST", "tasks/a/cancel", nil},
		{"dreama-local-preflight", "GET", "tasks", map[string]string{"scope": "exceptions"}},
		{"dreama-local-preflight", "GET", "tasks", map[string]string{"scope": "all", "canvasId": "canvas-a"}},
	} {
		if _, err := svc.Exchange(context.Background(), request.channel, request.method, request.path, request.query, nil); err == nil {
			t.Error("unsafe request accepted")
		}
	}
	if calls != 0 {
		t.Fatal("invalid request reached upstream")
	}
}

func TestExchangeScopesExceptionsToCanvas(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || r.URL.Path != "/integration/v1/tasks" ||
			r.URL.Query().Get("canvasId") != "canvas-a" || r.URL.Query().Get("scope") != "exceptions" {
			t.Errorf("unexpected exception query %s %s", r.Method, r.URL)
		}
		_, _ = w.Write([]byte(`{"items":[{"id":"needs-review","status":"submission_unknown"}]}`))
	}))
	defer server.Close()
	body, err := New(fixture(t, server.URL)).Exchange(context.Background(), "dreama-local-preflight", http.MethodGet,
		"tasks", map[string]string{"scope": "exceptions", "canvasId": "canvas-a"}, nil)
	if err != nil || !strings.Contains(string(body), "needs-review") {
		t.Fatalf("exception query: %s %v", body, err)
	}
}
func TestExchangeDoesNotFollowRedirectOrExposeCredentials(t *testing.T) {
	for _, mode := range []string{"redirect", "error", "echo"} {
		t.Run(mode, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if mode == "redirect" {
					http.Redirect(w, r, "https://example.invalid/steal", 302)
					return
				}
				if mode == "error" {
					w.WriteHeader(500)
				}
				w.Write([]byte(`{"message":"fixture-secret"}`))
			}))
			defer server.Close()
			data, err := New(fixture(t, server.URL)).Exchange(context.Background(), "dreama-local-preflight", "GET", "status", nil, nil)
			if err == nil || strings.Contains(err.Error(), "fixture-secret") || len(data) > 0 {
				t.Fatal("unsafe bridge response escaped")
			}
		})
	}
}
func TestRefreshPreservesKeysDefaultsAndOtherChannelsWithCAS(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"schemaVersion":1,"catalogVersion":"v2","channels":[{"id":"dreama-local-preflight","models":["new"],"modelProfiles":[{"model":"new","protocol":"dreama-videos-async","capability":"video"}]}],"models":[]}`))
	}))
	defer server.Close()
	config := fixture(t, server.URL)
	svc := New(config)
	result, err := svc.Refresh(context.Background(), "dreama-local-preflight", 4)
	if err != nil || result.Revision != 5 || config.writes != 1 {
		t.Fatalf("refresh: %#v %v", result, err)
	}
	var body map[string]any
	json.Unmarshal(config.body, &body)
	if body["videoModel"] != "dreama-local-preflight::old" || body["assistantModel"] != "codex::gpt" {
		t.Fatal("defaults changed")
	}
	channels := body["channels"].([]any)
	if channels[0].(map[string]any)["apiKey"] != "fixture-secret" || channels[1].(map[string]any)["apiKey"] != "keep-codex" {
		t.Fatal("credentials changed")
	}
	if _, err = svc.Refresh(context.Background(), "dreama-local-preflight", 4); err == nil || config.writes != 1 {
		t.Fatal("stale refresh accepted")
	}
}
func TestRefreshRejectsForeignChannelAndMalformedProfile(t *testing.T) {
	for _, catalog := range []string{
		`{"schemaVersion":1,"channels":[{"id":"codex","models":["new"],"modelProfiles":[{"model":"new"}]}]}`,
		`{"schemaVersion":1,"channels":[{"id":"dreama-local-preflight","models":["new"],"modelProfiles":[{"model":"different","protocol":"dreama-videos-async"}]}]}`,
	} {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(catalog)) }))
		config := fixture(t, server.URL)
		_, err := New(config).Refresh(context.Background(), "dreama-local-preflight", 4)
		server.Close()
		if err == nil || config.writes != 0 {
			t.Fatal("invalid catalog mutated configuration")
		}
	}
}

func TestRefreshSkipsDisabledAndPinnedChannels(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"schemaVersion":1,"channels":[{"id":"dreama-local-preflight","models":["new"],"modelProfiles":[{"model":"new","protocol":"dreama-videos-async"}]},{"id":"dreama-disabled","models":["new"],"modelProfiles":[{"model":"new","protocol":"dreama-images-async"}]},{"id":"dreama-pinned","models":["new"],"modelProfiles":[{"model":"new","protocol":"dreama-images-async"}]}]}`))
	}))
	defer server.Close()
	config := fixture(t, server.URL)
	var body map[string]any
	json.Unmarshal(config.body, &body)
	channels := body["channels"].([]any)
	disabled := map[string]any{"id": "dreama-disabled", "enabled": false, "models": []string{"old"}}
	pinned := map[string]any{"id": "dreama-pinned", "enabled": true, "pinned": true, "models": []string{"old"}}
	body["channels"] = append(channels, disabled, pinned)
	config.body, _ = json.Marshal(body)
	result, err := New(config).Refresh(context.Background(), "dreama-local-preflight", 4)
	if err != nil || len(result.UpdatedChannels) != 1 {
		t.Fatalf("refresh: %#v %v", result, err)
	}
	json.Unmarshal(config.body, &body)
	for _, item := range body["channels"].([]any)[2:] {
		if item.(map[string]any)["models"].([]any)[0] != "old" {
			t.Fatal("inactive channel was changed")
		}
	}
}
