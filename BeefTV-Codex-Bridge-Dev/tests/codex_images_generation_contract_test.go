// Run through a Go overlay against BeefTV's internal/generation package.
package generation

import (
	"context"
	"encoding/json"
	"os"
	"testing"

	"infinite-canvas/backend/internal/protocol"
)

func TestLocalCodexImageGenerationContract(t *testing.T) {
	data, err := os.ReadFile(os.Getenv("CODEX_IMAGE_PLUGIN_TEST_PACKAGE"))
	if err != nil {
		t.Fatal(err)
	}
	pkg, err := protocol.ParsePluginPackage(data)
	if err != nil {
		t.Fatal(err)
	}
	adapters, err := protocol.LoadInstalledProviders(pkg.ManifestRaw, nil)
	if err != nil || len(adapters) != 1 {
		t.Fatalf("adapters: %d, %v", len(adapters), err)
	}
	adapter := adapters[0]
	const fixturePNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
	input := Input{Mode: "image", Prompt: "fixture only", Config: Config{
		BaseURL: "http://127.0.0.1:18766", Model: "codex-image", InterfaceType: "codex-image",
		Count: "1", Size: "9:16", TransparentBackground: "true",
		ReferenceMediaTransport: map[string]string{"image": "inline"},
	}, ReferenceImages: []Media{{DataURL: fixturePNG, MimeType: "image/png"}}}
	request := ProtocolRequestFromInput(input)
	request.Extra["idempotencyKey"] = "fixture-task-identity"
	spec, err := adapter.BuildCreate(context.Background(), protocol.RequestContext{BaseURL: input.Config.BaseURL, Request: request})
	if err != nil {
		t.Fatal(err)
	}
	url, err := ProtocolRequestURL(input.Config.BaseURL+"/v1", spec)
	if err != nil || url != "http://127.0.0.1:18766/v1/images/jobs" {
		t.Fatalf("URL: %q, %v", url, err)
	}
	body := spec.Body.(map[string]any)
	images := body["images"].([]any)
	if len(images) != 1 || images[0] != fixturePNG || body["transparentBackground"] != true || body["aspectRatio"] != "9:16" || body["prompt"] != input.Prompt {
		t.Fatalf("actual generation input mapping: %#v", body)
	}
	response, _ := json.Marshal(map[string]any{"id": "image-job-1", "status": "succeeded", "images": []any{map[string]any{"dataUrl": fixturePNG, "mimeType": "image/png"}}})
	parsed, err := adapter.ParsePoll(context.Background(), protocol.PollContext{TaskID: "image-job-1", Request: request}, response)
	if err != nil || parsed.Status != protocol.StatusSucceeded {
		t.Fatalf("poll: %#v, %v", parsed, err)
	}
	finished, err := FinishProtocolAdapterResult(context.Background(), input, adapter, request, parsed.TaskID, parsed.Result, VideoPollPolicy{})
	if err != nil {
		t.Fatal(err)
	}
	outputs := finished["images"].([]interface{})
	if finished["mode"] != "image" || len(outputs) != 1 {
		t.Fatalf("delivery: %#v", finished)
	}
	output := outputs[0].(map[string]interface{})
	if output["mimeType"] != "image/png" || output["dataUrl"] != fixturePNG {
		t.Fatalf("output: %#v", output)
	}
	input.Mask = &Media{DataURL: fixturePNG}
	if _, err := adapter.BuildCreate(context.Background(), protocol.RequestContext{Request: ProtocolRequestFromInput(input)}); err == nil {
		t.Fatal("mask from actual generation input was not rejected")
	}
}
