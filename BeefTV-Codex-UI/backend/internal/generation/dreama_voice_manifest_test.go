package generation

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"testing"

	"infinite-canvas/backend/internal/protocol"
)

// This fixture is the installable Dreama 1.1.0 package manifest, not a mock adapter.
// It exercises Go's typed ProviderOptions projection and the plugin expression runtime.
func TestDreamaVoiceManifestRoundTrip(t *testing.T) {
	manifest, err := os.ReadFile("testdata/dreama-voice-manifest.json")
	if override := os.Getenv("BEEFTV_DREAMA_TEST_MANIFEST"); override != "" {
		manifest, err = os.ReadFile(override)
	}
	if err != nil {
		t.Fatal(err)
	}
	adapters, err := protocol.LoadInstalledProviders(manifest, nil)
	if err != nil {
		t.Fatal(err)
	}
	byID := make(map[string]protocol.Adapter)
	for _, adapter := range adapters {
		byID[adapter.Metadata().ID] = adapter
	}
	for _, id := range []string{"dreama-videos-async", "dreama-extension-async", "dreama-audio-async"} {
		t.Run(id, func(t *testing.T) {
			mode, promptKey := "video", "prompt"
			if id == "dreama-audio-async" {
				mode, promptKey = "audio", "input"
			}
			voices := []any{map[string]any{"image_index": float64(1), "name": "角色原名", "voice_id": "stable-voice"}}
			if mode == "video" {
				voices = append(voices, map[string]any{"image_index": float64(2), "name": "样本角色", "audio": "data:audio/mpeg;base64,YXVkaW8="})
			}
			input := Input{Mode: mode, Prompt: "  原文第一行\n@图片1 对 @图片2 说：你好！  ",
				Config:   Config{InterfaceType: id, Model: "existing-model-id", Size: "9:16", VQuality: "720p", VideoSeconds: "7"},
				Metadata: map[string]any{"providerOptions": map[string]any{id: map[string]any{"character_voices": voices}}, "videoEditOperation": "reference_to_video"},
			}
			if mode == "video" {
				input.ReferenceImages = []Media{{ID: "second-in-canvas", DataURL: "data:image/png;base64,Yg=="}, {ID: "first-in-canvas", DataURL: "data:image/png;base64,YQ=="}}
			}
			if id == "dreama-extension-async" {
				input.ReferenceVideos = []Media{{ID: "original", DataURL: "data:video/mp4;base64,dmlkZW8="}}
			}
			// Round-trip through persisted task JSON exactly as task dispatch does.
			encoded, _ := json.Marshal(input)
			var restored Input
			if err := json.Unmarshal(encoded, &restored); err != nil {
				t.Fatal(err)
			}
			request := ProtocolRequestFromInput(restored)
			request.Extra["idempotencyKey"] = "same-operation-no-retry"
			adapter := byID[id]
			if adapter == nil {
				t.Fatal("provider missing")
			}
			spec, err := adapter.BuildCreate(context.Background(), protocol.RequestContext{Request: request})
			if err != nil {
				t.Fatal(err)
			}
			body := spec.Body.(map[string]any)
			if body[promptKey] != input.Prompt || body["model"] != input.Config.Model {
				t.Fatalf("prompt/model changed: %#v", body)
			}
			if !reflect.DeepEqual(body["character_voices"], voices) {
				t.Fatalf("voice metadata lost: %#v", body["character_voices"])
			}
			if spec.Headers["Idempotency-Key"] != "same-operation-no-retry" {
				t.Fatalf("idempotency key lost: %#v", spec.Headers)
			}
			if mode == "video" {
				if !reflect.DeepEqual(body["images"], []any{"data:image/png;base64,Yg==", "data:image/png;base64,YQ=="}) {
					t.Fatalf("image order changed: %#v", body["images"])
				}
				if body["duration"] != 7 || body["resolution"] != "720p" {
					t.Fatalf("video settings changed: %#v", body)
				}
				if id == "dreama-extension-async" && !reflect.DeepEqual(body["videos"], []any{"data:video/mp4;base64,dmlkZW8="}) {
					t.Fatalf("source video lost: %#v", body["videos"])
				}
			} else if _, exists := body["images"]; exists {
				t.Fatal("identity image leaked into audio generation")
			}
			created, err := adapter.ParseCreate(context.Background(), []byte(`{"id":"existing-job","status":"pending"}`))
			if err != nil || created.TaskID != "existing-job" || created.Status != protocol.StatusPending {
				t.Fatalf("create: %#v, %v", created, err)
			}
			response := `{"id":"existing-job","status":"completed","video_url":"http://127.0.0.1:18765/media/result.mp4"}`
			if mode == "audio" {
				response = `{"id":"existing-job","status":"succeeded","audio_url":"http://127.0.0.1:18765/media/result.mp3"}`
			}
			completed, err := adapter.ParsePoll(context.Background(), protocol.PollContext{TaskID: "existing-job"}, []byte(response))
			if err != nil || completed.Status != protocol.StatusSucceeded || completed.Result == nil {
				t.Fatalf("result: %#v, %v", completed, err)
			}
			if mode == "video" && (len(completed.Result.Videos) != 1 || !completed.Result.Videos[0].Ephemeral) {
				t.Fatalf("video return lost: %#v", completed.Result)
			}
			if mode == "audio" && (len(completed.Result.Audios) != 1 || !completed.Result.Audios[0].Ephemeral) {
				t.Fatalf("audio return lost: %#v", completed.Result)
			}
		})
	}
}
