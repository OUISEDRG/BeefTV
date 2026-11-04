package workspace

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func voiceFixtureService(t *testing.T, baseURL string, change func(map[string]any)) *ModelVoiceCatalogService {
	t.Helper()
	c := map[string]any{"id": "audio-local", "enabled": true, "baseUrl": baseURL + "/audio", "apiKey": "test-secret-very-private", "models": []string{"audio-model"}, "modelProfiles": []any{map[string]any{"model": "audio-model", "capability": "audio", "protocol": "custom-audio"}}}
	if change != nil {
		change(c)
	}
	raw, _ := json.Marshal(map[string]any{"channels": []any{c}})
	return NewModelVoiceCatalogService(reasoningFixtureConfig(raw))
}
func TestModelVoicesStoredCredentialsAndProjection(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "GET" || r.URL.Path != "/audio/v1/voices" || r.Header.Get("Authorization") != "Bearer test-secret-very-private" {
			t.Error("wrong authenticated catalog request")
		}
		if r.Header.Get("Cookie") != "" || r.Header.Get("Origin") != "" {
			t.Error("browser headers forwarded")
		}
		w.Write([]byte(`{"voices":[{"id":"5","name":"My voice","source":"personal","previewUrl":"https://example.com/voice.mp3","token":"test-secret-very-private"}],"secret":"test-secret-very-private"}`))
	}))
	defer server.Close()
	got, err := voiceFixtureService(t, server.URL, nil).ReadModelVoices(context.Background(), "audio-local")
	if err != nil || len(got.Voices) != 1 || got.ChannelID != "audio-local" {
		t.Fatalf("catalog failed: %v", err)
	}
	raw, _ := json.Marshal(got)
	if strings.Contains(string(raw), "test-secret") {
		t.Fatal("credential leaked")
	}
}
func TestModelVoicesRejectUnsafeChannels(t *testing.T) {
	for _, change := range []func(map[string]any){
		func(c map[string]any) { c["enabled"] = false }, func(c map[string]any) { c["baseUrl"] = "https://example.com/audio" },
		func(c map[string]any) { c["baseUrl"] = "http://127.0.0.1:1234/audio?key=x" }, func(c map[string]any) { c["apiKey"] = "" },
		func(c map[string]any) { c["modelProfiles"] = []any{} }, func(c map[string]any) { c["credentialRef"] = "managed" },
	} {
		if _, err := voiceFixtureService(t, "http://127.0.0.1:1234", change).ReadModelVoices(context.Background(), "audio-local"); err == nil {
			t.Fatal("invalid channel accepted")
		}
	}
}
func TestModelVoicesRejectUnsafeResponses(t *testing.T) {
	for _, response := range []string{
		`{"voices":[{"id":"1","name":"test-secret-very-private","source":"public"}]}`,
		`{"voices":[{"id":"1","name":"voice","source":"public","previewUrl":"javascript:alert(1)"}]}`,
		`{"voices":[{"id":"1","name":"voice","source":"public","previewUrl":"https://user:pass@example.com/a.mp3"}]}`,
		`{"voices":[{"id":"1","name":"voice","source":"public"},{"id":"1","name":"other","source":"personal"}]}`,
		`{"voices":null}`, `{"error":"not a catalog"}`,
	} {
		s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(response)) }))
		_, err := voiceFixtureService(t, s.URL, nil).ReadModelVoices(context.Background(), "audio-local")
		s.Close()
		if err == nil {
			t.Fatal("unsafe response accepted")
		}
	}
}
func TestModelVoicesDoNotFollowRedirects(t *testing.T) {
	reached := false
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { reached = true }))
	defer target.Close()
	redirect := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, target.URL, 302) }))
	defer redirect.Close()
	_, err := voiceFixtureService(t, redirect.URL, nil).ReadModelVoices(context.Background(), "audio-local")
	if err == nil || reached {
		t.Fatal("redirect followed")
	}
}
