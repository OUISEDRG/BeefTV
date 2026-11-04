package protocol

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

// The dedicated build supplies the independently versioned bridge manifest.
// This exercises the real Go expression compiler, without sending any HTTP.
func TestDreamaReleaseContract(t *testing.T) {
	path := os.Getenv("BEEFTV_DREAMA_MANIFEST")
	if path == "" {
		t.Skip("dedicated release supplies its bridge manifest")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	providers, err := LoadInstalledProviders(raw, nil)
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]bool{"dreama-videos-async": false, "dreama-images-async": false, "dreama-extension-async": false, "dreama-enhancement-async": false, "dreama-audio-async": false}
	binding := map[string]any{"schemaVersion": float64(1), "canvasId": "canvas-a", "nodeId": "node-b", "projectId": float64(12), "seriesId": float64(34), "promptMode": "drama", "aspectRatio": "16:9", "bindingRevision": float64(2)}
	for _, provider := range providers {
		id := provider.Metadata().ID
		if _, ok := want[id]; !ok {
			continue
		}
		want[id] = true
		t.Run(id, func(t *testing.T) {
			prompt := "  原文\n@图片2 后 @图片1，再 @图片2。  "
			if id == "dreama-enhancement-async" {
				prompt = ""
			}
			request := GenerationRequest{Model: "fixture", Prompt: prompt, Duration: 5, AspectRatio: "16:9", Quality: "3K", Resolution: "1080p", ImageCount: 1,
				Images:          []MediaReference{{URL: "https://example.invalid/a.png", Order: 1}, {URL: "https://example.invalid/b.png", Order: 2}},
				ProviderOptions: map[string]map[string]any{"dreama": binding}, Extra: map[string]any{"idempotencyKey": "stable-request", "audioVoice": "fixture-voice"}}
			created, err := provider.BuildCreate(context.Background(), RequestContext{BaseURL: "http://127.0.0.1:18765/v1", Request: request})
			if err != nil {
				t.Fatal(err)
			}
			encoded, err := json.Marshal(created.Body)
			if err != nil {
				t.Fatal(err)
			}
			var body map[string]any
			if err := json.Unmarshal(encoded, &body); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(body["dreama"], binding) {
				t.Fatal("canvas binding did not survive plugin compilation")
			}
			field := "prompt"
			if id == "dreama-audio-async" {
				field = "input"
			}
			if body[field] != prompt {
				t.Fatal("plugin rewrote prompt text")
			}
			if created.Headers["Idempotency-Key"] != "stable-request" {
				t.Fatal("stable request identity lost")
			}
			if id == "dreama-images-async" && body["resolution"] != "3K" {
				t.Fatal("native image tier lost")
			}
		})
	}
	for id, found := range want {
		if !found {
			t.Errorf("missing provider %s", id)
		}
	}
}
