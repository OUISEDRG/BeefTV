package operations

import "testing"

func TestStandaloneGenerationRequiresCanvasForDreama(t *testing.T) {
	for _, protocol := range []string{"dreama-images-async", "dreama-videos-async", "dreama-extension-async", "dreama-enhancement-async", "dreama-audio-async"} {
		err := ValidateStandaloneGenerationProtocol(protocol)
		if err == nil || AsError(err).Reason != "generation_requires_canvas_binding" {
			t.Fatalf("protocol %s was not blocked before task creation: %v", protocol, err)
		}
	}
	for _, protocol := range []string{"openai-image", "chat-completion", "openai-response"} {
		if err := ValidateStandaloneGenerationProtocol(protocol); err != nil {
			t.Fatalf("unrelated protocol %s blocked: %v", protocol, err)
		}
	}
}
