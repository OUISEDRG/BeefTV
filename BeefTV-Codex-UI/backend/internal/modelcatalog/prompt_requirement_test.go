package modelcatalog

import (
	"encoding/json"
	"testing"
)

func TestSavedVideoPromptRequirementMatchesEnabledIdentity(t *testing.T) {
	optional := false
	video := DefaultModelCapabilityConfig("openai-videos").Video
	video.PromptRequired = &optional
	profile := map[string]any{"model": "opaque-upscale", "protocol": "openai-videos", "capability": "video", "videoCapabilitiesVersion": "v1", "capabilityConfig": &ModelCapabilityConfig{Version: 1, Video: video}}
	channel := map[string]any{"id": "saved", "scope": "user", "enabled": true, "baseUrl": "http://127.0.0.1:8123/v1", "models": []string{"opaque-upscale"}, "modelProfiles": []any{profile}}
	read := func() ([]byte, error) { return json.Marshal(map[string]any{"channels": []any{channel}}) }
	identity := PromptModelIdentity{Model: "opaque-upscale", Protocol: "openai-videos", BaseURL: "http://127.0.0.1:8123/v1"}
	allowed, err := ResolvePromptOptional(identity, read, nil)
	if err != nil || !allowed {
		t.Fatalf("saved declaration not accepted: %v %v", allowed, err)
	}
	for _, mutation := range []func(){func() { channel["enabled"] = false }, func() { channel["scope"] = "system" }, func() { channel["baseUrl"] = "http://127.0.0.1:9123/v1" }, func() { profile["model"] = "other" }, func() { profile["protocol"] = "newapi" }} {
		channel["enabled"], channel["scope"], channel["baseUrl"] = true, "user", identity.BaseURL
		profile["model"], profile["protocol"] = identity.Model, identity.Protocol
		mutation()
		if allowed, err := ResolvePromptOptional(identity, read, nil); err != nil || allowed {
			t.Fatalf("mismatched identity accepted: %v %v", allowed, err)
		}
	}
}
