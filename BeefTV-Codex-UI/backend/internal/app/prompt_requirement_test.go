package app

import (
	"encoding/json"
	"infinite-canvas/backend/internal/modelcatalog"
	"testing"
)

func TestGenerationPromptExemptionRequiresSavedProfile(t *testing.T) {
	t.Setenv("CANVAS_ALLOW_PRIVATE_UPSTREAMS", "true")
	service := &Service{dataDir: t.TempDir()}
	optional := false
	capability := modelcatalog.DefaultModelCapabilityConfig("openai-videos")
	capability.Video.PromptRequired = &optional
	request := providerConfig{BaseURL: "http://127.0.0.1:8123/v1", InterfaceType: "openai-videos", Model: "opaque-upscale", CapabilityConfig: capability}
	resolved, err := (appConfigPort{service: service}).Resolve(request)
	if err != nil || resolved.AllowEmptyPrompt {
		t.Fatalf("client-only flag accepted: %v %v", resolved.AllowEmptyPrompt, err)
	}
	body, _ := json.Marshal(map[string]any{"channels": []any{map[string]any{
		"id": "saved", "scope": "user", "enabled": true, "baseUrl": request.BaseURL, "models": []string{request.Model},
		"modelProfiles": []any{map[string]any{"model": request.Model, "protocol": request.InterfaceType, "capability": "video", "videoCapabilitiesVersion": "v1", "capabilityConfig": capability}},
	}}})
	if err := service.SaveLocalModelConfig(body); err != nil {
		t.Fatal(err)
	}
	resolved, err = (appConfigPort{service: service}).Resolve(request)
	if err != nil || !resolved.AllowEmptyPrompt {
		t.Fatalf("saved flag not accepted: %v %v", resolved.AllowEmptyPrompt, err)
	}
}
