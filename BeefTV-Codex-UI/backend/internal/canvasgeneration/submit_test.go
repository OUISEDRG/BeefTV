package canvasgeneration

import (
	"encoding/json"
	"testing"
)

func TestTaskInputPreservesCompiledOrderBindingAndTypedOptions(t *testing.T) {
	item := fixtureOutput().Items[0]
	item.Config = map[string]any{"model": "eid", "interfaceType": "dreama-videos-async", "videoSeconds": 19, "videoGenerateAudio": false, "vquality": "480P"}
	item.ReferenceImages = []Reference{{ID: "z", StorageKey: "resource:z"}, {ID: "a", StorageKey: "resource:a"}}
	item.Metadata = map[string]any{"providerOptions": map[string]any{"dreama": map[string]any{"seriesId": 12}, "voices": map[string]any{"1": "voice-z"}}}
	input, err := TaskInput(item, map[string]any{"model": "eid", "interfaceType": "dreama-videos-async", "apiKey": "PRIVATE", "baseUrl": "http://localhost"})
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(input)
	var got struct {
		Prompt          string
		Config          map[string]any
		Metadata        map[string]any
		ReferenceImages []Reference
	}
	json.Unmarshal(raw, &got)
	if got.Prompt != "正文 @图片1" || got.ReferenceImages[0].ID != "z" || got.ReferenceImages[1].ID != "a" || got.Config["videoSeconds"] != float64(19) || got.Config["videoGenerateAudio"] != false || got.Config["apiKey"] != "PRIVATE" {
		t.Fatalf("lost compiled inputs: %s", raw)
	}
	options := got.Metadata["providerOptions"].(map[string]any)
	if options["dreama"].(map[string]any)["seriesId"] != float64(12) || options["voices"].(map[string]any)["1"] != "voice-z" {
		t.Fatal("binding or voice changed")
	}
}

func TestTaskInputRejectsCredentialInjectionAndChangedModel(t *testing.T) {
	for _, field := range []string{"apiKey", "baseUrl", "credentialRef"} {
		item := fixtureOutput().Items[0]
		item.Config["model"] = "eid"
		item.Config[field] = "injected"
		if _, err := TaskInput(item, map[string]any{"model": "eid", "interfaceType": "dreama-videos-async"}); err == nil {
			t.Fatal("accepted compiler credential", field)
		}
	}
	item := fixtureOutput().Items[0]
	item.Config["model"] = "old"
	if _, err := TaskInput(item, map[string]any{"model": "new", "interfaceType": "dreama-videos-async"}); err == nil {
		t.Fatal("accepted changed model")
	}
}
