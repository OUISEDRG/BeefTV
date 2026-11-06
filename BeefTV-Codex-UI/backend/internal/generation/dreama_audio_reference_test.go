package generation

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"reflect"
	"strings"
	"testing"

	"infinite-canvas/backend/internal/protocol"
)

type dreamaAudioResources struct{ ResourcePort }

func (dreamaAudioResources) LocalMode() bool { return true }
func (dreamaAudioResources) Lookup(owner, id string) (ResourceInfo, error) {
	if owner != "owner" || (id != "audio-first" && id != "audio-second" && id != "voice-sample" && id != "video-first" && id != "video-second" && id != "video-third") {
		return ResourceInfo{}, fmt.Errorf("resource unavailable")
	}
	info := ResourceInfo{ID: id, UserID: owner, Kind: "audio", Status: "ready", MimeType: "audio/mpeg", Size: int64(len(id)), DurationMs: 2000}
	if strings.HasPrefix(id, "video-") {
		info.Kind, info.MimeType, info.Width, info.Height = "video", "video/mp4", 1280, 720
	}
	return info, nil
}
func (p dreamaAudioResources) Open(owner, id string) (ResourceInfo, io.ReadCloser, error) {
	info, err := p.Lookup(owner, id)
	return info, io.NopCloser(strings.NewReader(id)), err
}

func TestDreamaOrdinaryAudioManifestRoundTrip(t *testing.T) {
	manifestPath := "testdata/dreama-voice-manifest.json"
	if override := os.Getenv("BEEFTV_DREAMA_TEST_MANIFEST"); override != "" {
		manifestPath = override
	}
	manifest, err := os.ReadFile(manifestPath)
	if err != nil {
		t.Fatal(err)
	}
	adapters, err := protocol.LoadInstalledProviders(manifest, nil)
	if err != nil {
		t.Fatal(err)
	}
	var adapter protocol.Adapter
	for _, item := range adapters {
		if item.Metadata().ID == "dreama-videos-async" {
			adapter = item
		}
	}
	if adapter == nil {
		t.Fatal("Dreama video provider missing")
	}
	for _, scenario := range []struct{ image, videos bool }{{}, {image: true}, {image: true, videos: true}} {
		t.Run(fmt.Sprintf("with_image_%v_videos_%v", scenario.image, scenario.videos), func(t *testing.T) {
			input := Input{Mode: "video", Prompt: "  @音频1 和 @音频2 的节奏\n生成画面。  ", Config: Config{InterfaceType: "dreama-videos-async", Model: "catalog-model", VideoSeconds: "5", VQuality: "720p"},
				ReferenceAudios: []Media{{ID: "second-in-canvas", StorageKey: "resource:audio-second"}, {ID: "first-in-canvas", StorageKey: "resource:audio-first"}},
				Metadata:        map[string]any{"videoEditOperation": "audio_to_video"},
			}
			if scenario.image {
				input.ReferenceImages = []Media{{ID: "person", DataURL: "data:image/png;base64,aW1hZ2U="}}
				input.Metadata["videoEditOperation"] = "image_to_video"
				input.Metadata["providerOptions"] = map[string]any{"dreama-videos-async": map[string]any{"character_voices": []any{map[string]any{"image_index": 1, "name": "人物声线", "sampleResourceId": "voice-sample"}}}}
			}
			if scenario.videos {
				input.ReferenceVideos = []Media{{ID: "third-clip", StorageKey: "resource:video-third"}, {ID: "first-clip", StorageKey: "resource:video-first"}, {ID: "second-clip", StorageKey: "resource:video-second"}}
				input.Metadata["videoEditOperation"] = "reference_to_video"
			}
			encoded, err := json.Marshal(input)
			if err != nil {
				t.Fatal(err)
			}
			var execution Input
			if err := json.Unmarshal(encoded, &execution); err != nil {
				t.Fatal(err)
			}
			ctx := WithRuntime(context.Background(), Runtime{Resources: dreamaAudioResources{}})
			if err := HydrateMedia(ctx, "owner", &execution, MediaHydrationPolicy{}); err != nil {
				t.Fatal(err)
			}
			request := ProtocolRequestFromInput(execution)
			if len(request.Audios) != 2 || request.Audios[0].ID != "second-in-canvas" || request.Audios[1].ID != "first-in-canvas" {
				t.Fatalf("ordinary audio identity/order changed: %#v", request.Audios)
			}
			for _, audio := range execution.ReferenceAudios {
				if audio.DurationMs != 2000 || audio.Bytes <= 0 || audio.MimeType != "audio/mpeg" {
					t.Fatalf("owned resource metadata lost: %#v", audio)
				}
			}
			spec, err := adapter.BuildCreate(ctx, protocol.RequestContext{Request: request})
			if err != nil {
				t.Fatal(err)
			}
			body := spec.Body.(map[string]any)
			if !reflect.DeepEqual(body["audios"], []any{"data:audio/mpeg;base64,YXVkaW8tc2Vjb25k", "data:audio/mpeg;base64,YXVkaW8tZmlyc3Q="}) {
				t.Fatalf("ordinary audio bytes/order lost: %#v", body["audios"])
			}
			if body["prompt"] != input.Prompt {
				t.Fatalf("prompt changed: %#v", body["prompt"])
			}
			if scenario.videos {
				if len(request.Videos) != 3 || request.Videos[0].ID != "third-clip" || request.Videos[1].ID != "first-clip" || request.Videos[2].ID != "second-clip" {
					t.Fatalf("ordinary video identity/order changed: %#v", request.Videos)
				}
				if !reflect.DeepEqual(body["videos"], []any{"data:video/mp4;base64,dmlkZW8tdGhpcmQ=", "data:video/mp4;base64,dmlkZW8tZmlyc3Q=", "data:video/mp4;base64,dmlkZW8tc2Vjb25k"}) {
					t.Fatalf("ordinary videos lost: %#v", body["videos"])
				}
				for _, video := range execution.ReferenceVideos {
					if video.DurationMs != 2000 || video.Bytes <= 0 || video.Width != 1280 || video.Height != 720 || video.MimeType != "video/mp4" {
						t.Fatalf("video resource metadata lost: %#v", video)
					}
				}
			}
			if scenario.image {
				voices := body["character_voices"].([]any)
				voice := voices[0].(map[string]any)
				if len(voices) != 1 || voice["audio"] != "data:audio/mpeg;base64,dm9pY2Utc2FtcGxl" || voice["image_index"] != float64(1) {
					t.Fatalf("voice binding mixed with ordinary audio: %#v", voices)
				}
			} else if _, present := body["character_voices"]; present {
				t.Fatal("ordinary audio was mistaken for a character voice")
			}
		})
	}
}
