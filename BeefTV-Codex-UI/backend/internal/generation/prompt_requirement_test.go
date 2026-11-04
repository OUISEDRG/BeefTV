package generation

import (
	"context"
	"strings"
	"testing"
)

type trustedPromptConfig struct {
	stageConfig
	allow bool
	calls int
}

func (p *trustedPromptConfig) Resolve(config Config) (Config, error) {
	p.calls++
	config.AllowEmptyPrompt = p.allow
	return config, nil
}

func TestExecutePromptRequirementUsesResolvedConfig(t *testing.T) {
	optional := false
	for _, allow := range []bool{false, true} {
		port := &trustedPromptConfig{allow: allow}
		ctx := WithRuntime(context.Background(), Runtime{Config: port})
		_, err := Execute(ctx, Input{Mode: "video", Prompt: "", Config: Config{CapabilityConfig: &ModelCapabilityConfig{Version: 1, Video: &VideoCapabilityConfig{PromptRequired: &optional}}}})
		if port.calls != 1 {
			t.Fatalf("prompt checked before trusted config resolution; calls=%d", port.calls)
		}
		if err == nil {
			t.Fatal("test runtime must stop before any submission")
		}
		if strings.Contains(err.Error(), "prompt is required") == allow {
			t.Fatalf("allow=%v err=%v", allow, err)
		}
	}
}
