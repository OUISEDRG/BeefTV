// Run through a Go overlay against BeefTV's internal/protocol package.
package protocol

import (
	"context"
	"encoding/json"
	"os"
	"testing"
)

func TestLocalCodexImagePackageContract(t *testing.T) {
	data, err := os.ReadFile(os.Getenv("CODEX_IMAGE_PLUGIN_TEST_PACKAGE"))
	if err != nil {
		t.Fatal(err)
	}
	pkg, err := ParsePluginPackage(data)
	if err != nil {
		t.Fatal(err)
	}
	adapters, err := LoadInstalledProviders(pkg.ManifestRaw, nil)
	if err != nil || len(adapters) != 1 {
		t.Fatalf("providers: %v, %v", len(adapters), err)
	}
	adapter := adapters[0]
	request := GenerationRequest{Capability: CapabilityImage, Model: "codex-image", Prompt: "fixture only", ImageCount: 1,
		AspectRatio: "16:9", Images: []MediaReference{{DataURL: "data:image/png;base64,ZmFrZQ==", Role: "reference", URL: "https://must-not-be-used.invalid"}},
		Output: OutputOptions{Options: map[string]any{"transparent": "true"}}, Extra: map[string]any{"idempotencyKey": "fixture-idempotency"}}
	create, err := adapter.BuildCreate(context.Background(), RequestContext{BaseURL: "http://127.0.0.1:18766", Request: request})
	if err != nil {
		t.Fatal(err)
	}
	if create.Method != "POST" || create.Path != "/v1/images/jobs" || !create.OriginPath || create.Headers["Idempotency-Key"] != "fixture-idempotency" {
		t.Fatalf("unexpected create contract: %#v", create)
	}
	body := create.Body.(map[string]any)
	if body["transparentBackground"] != true || body["aspectRatio"] != "16:9" || body["model"] != "codex-image" {
		t.Fatalf("body: %#v", body)
	}
	images := body["images"].([]any)
	if len(images) != 1 || images[0] != request.Images[0].DataURL {
		t.Fatalf("images: %#v", images)
	}
	if _, exists := body["size"]; exists {
		t.Fatal("hard size unexpectedly sent")
	}
	poll, err := adapter.BuildPoll(context.Background(), PollContext{TaskID: "image-job-1"})
	if err != nil || poll.Method != "GET" || poll.Path != "/v1/images/jobs/image-job-1" || !poll.OriginPath {
		t.Fatalf("poll: %#v, %v", poll, err)
	}
	cancel, err := adapter.BuildCancel(context.Background(), PollContext{TaskID: "image-job-1"})
	if err != nil || cancel.Method != "DELETE" || cancel.Path != poll.Path || !cancel.OriginPath {
		t.Fatalf("cancel: %#v, %v", cancel, err)
	}
	for rawStatus, expected := range map[string]Status{"queued": StatusPending, "running": StatusProcessing, "failed": StatusFailed, "cancelled": StatusCancelled} {
		response, _ := json.Marshal(map[string]any{"id": "image-job-1", "status": rawStatus, "message": "fixture status"})
		parsed, err := adapter.ParseCreate(context.Background(), response)
		if err != nil || parsed.TaskID != "image-job-1" || parsed.Status != expected || parsed.Message != "fixture status" {
			t.Fatalf("status %s: %#v, %v", rawStatus, parsed, err)
		}
	}
	succeeded, err := adapter.ParsePoll(context.Background(), PollContext{TaskID: "image-job-1"}, []byte(`{"id":"image-job-1","status":"succeeded","images":[{"dataUrl":"data:image/png;base64,ZmFrZQ==","mimeType":"image/png"}]}`))
	if err != nil || succeeded.Status != StatusSucceeded || succeeded.Result == nil || len(succeeded.Result.Images) != 1 || succeeded.Result.Images[0].DataURL != request.Images[0].DataURL {
		t.Fatalf("success: %#v, %v", succeeded, err)
	}
	for name, invalid := range map[string]GenerationRequest{
		"mask":                {Model: "codex-image", Images: []MediaReference{{DataURL: "data:image/png;base64,ZmFrZQ==", Role: "mask"}}},
		"url":                 {Model: "codex-image", Images: []MediaReference{{URL: "https://example.invalid/image.png"}}},
		"multiple outputs":    {Model: "codex-image", ImageCount: 2},
		"too many references": {Model: "codex-image", Images: make([]MediaReference, 5)},
		"video":               {Model: "codex-image", Videos: []MediaReference{{URL: "https://example.invalid/video.mp4"}}},
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := adapter.BuildCreate(context.Background(), RequestContext{Request: invalid}); err == nil {
				t.Fatal("unsupported input was accepted")
			}
		})
	}
}
