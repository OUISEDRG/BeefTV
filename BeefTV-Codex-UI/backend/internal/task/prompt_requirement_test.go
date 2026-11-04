package task

import "testing"

type optionalPromptCatalog struct{ passCatalog }

func (optionalPromptCatalog) Select(_ string, req SelectRequest) (SelectResult, error) {
	return SelectResult{Input: req.Input, AllowEmptyPrompt: true}, nil
}

func TestAdmitUsesSelectedPromptRequirement(t *testing.T) {
	request := CreateRequest{Type: "canvas_video", Prompt: "", Input: map[string]any{"mode": "video", "config": map[string]any{"capabilityConfig": map[string]any{"video": map[string]any{"promptRequired": false}}}}}
	untrusted := domainService(newMemStore(), nil)
	if _, err := untrusted.CreateTask("user", request); err == nil {
		t.Fatal("client capability bypassed required prompt")
	}
	trusted := domainService(newMemStore(), func(deps *Dependencies) { deps.Catalog = optionalPromptCatalog{} })
	created, err := trusted.CreateTask("user", request)
	if err != nil {
		t.Fatal(err)
	}
	if created.Prompt != "" {
		t.Fatalf("invented prompt %q", created.Prompt)
	}
	request.Prompt = "  第一行\n第二行  "
	created, err = trusted.CreateTask("user", request)
	if err != nil || created.Prompt != request.Prompt {
		t.Fatalf("prompt text changed: %#v %v", created, err)
	}
	request.Prompt = ""
	request.Type = "canvas_image"
	request.Input["mode"] = "image"
	if _, err = trusted.CreateTask("user", request); err == nil {
		t.Fatal("video exemption allowed empty image prompt")
	}
}
