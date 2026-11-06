package generation

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"strings"
	"testing"

	"infinite-canvas/backend/internal/asset"
)

type dreamaSampleResources struct {
	ResourcePort
	info ResourceInfo
	body string
	deny bool
}

func (p dreamaSampleResources) Lookup(userID, id string) (ResourceInfo, error) {
	if p.deny || userID != "owner" || id != "voice-sample" {
		return ResourceInfo{}, errors.New("resource not owned")
	}
	return p.info, nil
}
func (p dreamaSampleResources) Open(userID, id string) (ResourceInfo, io.ReadCloser, error) {
	info, err := p.Lookup(userID, id)
	if err != nil {
		return info, nil, err
	}
	return info, io.NopCloser(strings.NewReader(p.body)), nil
}

func dreamaSampleInput(protocol string) Input {
	mode := "video"
	if protocol == "dreama-audio-async" {
		mode = "audio"
	}
	return Input{Mode: mode, Config: Config{InterfaceType: protocol}, Metadata: map[string]any{
		"providerOptions": map[string]any{protocol: map[string]any{"retained": true, "character_voices": []any{
			map[string]any{"image_index": float64(2), "name": "本人音色", "sampleResourceId": "voice-sample"},
			map[string]any{"image_index": float64(1), "name": "目录音色", "voice_id": "catalog-voice"},
		}}},
	}}
}

func readyDreamaSample() dreamaSampleResources {
	return dreamaSampleResources{info: ResourceInfo{UserID: "owner", ID: "voice-sample", Status: "ready", Kind: "audio", MimeType: "audio/mpeg", Size: 5}, body: "audio"}
}

func TestDreamaSampleResourcesHydrateOnlyExecutionCopy(t *testing.T) {
	for _, protocol := range []string{"dreama-videos-async", "dreama-extension-async", "dreama-audio-async"} {
		t.Run(protocol, func(t *testing.T) {
			persisted := dreamaSampleInput(protocol)
			before, _ := json.Marshal(persisted)
			if asset.ContainsInlineMediaDataURL(persisted.Metadata) {
				t.Fatal("persisted request contains inline media")
			}
			execution := persisted
			ctx := WithRuntime(context.Background(), Runtime{Resources: readyDreamaSample()})
			if err := HydrateMedia(ctx, "owner", &execution, MediaHydrationPolicy{}); err != nil {
				t.Fatal(err)
			}
			voices := ProtocolRequestFromInput(execution).ProviderOptions[protocol]["character_voices"].([]any)
			first := voices[0].(map[string]any)
			if first["audio"] != "data:audio/mpeg;base64,YXVkaW8=" || first["image_index"] != float64(2) || first["name"] != "本人音色" {
				t.Fatalf("voice lost: %#v", first)
			}
			if _, exists := first["sampleResourceId"]; exists {
				t.Fatal("local resource id leaked upstream")
			}
			if voices[1].(map[string]any)["voice_id"] != "catalog-voice" {
				t.Fatal("catalog voice changed")
			}
			after, _ := json.Marshal(persisted)
			if string(before) != string(after) {
				t.Fatal("execution mutated persisted request")
			}
			if !asset.ContainsInlineMediaDataURL(execution.Metadata) {
				t.Fatal("execution fixture should still be rejected if submitted as persisted task input")
			}
		})
	}
}

func TestDreamaSampleResourceRejection(t *testing.T) {
	for _, name := range []string{"not owned", "pending", "not audio", "declared empty", "declared oversized", "actual oversized", "empty", "invalid id", "conflicting audio", "conflicting catalog", "cancelled"} {
		t.Run(name, func(t *testing.T) {
			resources := readyDreamaSample()
			input := dreamaSampleInput("dreama-videos-async")
			voice := input.Metadata["providerOptions"].(map[string]any)["dreama-videos-async"].(map[string]any)["character_voices"].([]any)[0].(map[string]any)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			switch name {
			case "not owned":
				resources.deny = true
			case "pending":
				resources.info.Status = "uploading"
			case "not audio":
				resources.info.MimeType = "image/png"
			case "declared empty":
				resources.info.Size = 0
			case "declared oversized":
				resources.info.Size = 20*1024*1024 + 1
			case "actual oversized":
				resources.body = strings.Repeat("x", 20*1024*1024+1)
			case "empty":
				resources.body = ""
			case "invalid id":
				voice["sampleResourceId"] = "../private"
			case "conflicting audio":
				voice["audio"] = "https://example.com/sound.mp3"
			case "conflicting catalog":
				voice["voice_id"] = "catalog"
			case "cancelled":
				cancel()
			}
			before, _ := json.Marshal(input)
			ctx = WithRuntime(ctx, Runtime{Resources: resources})
			if err := HydrateMedia(ctx, "owner", &input, MediaHydrationPolicy{}); err == nil {
				t.Fatal("unsafe sample accepted")
			}
			after, _ := json.Marshal(input)
			if string(before) != string(after) {
				t.Fatal("failure mutated source input")
			}
		})
	}
}
