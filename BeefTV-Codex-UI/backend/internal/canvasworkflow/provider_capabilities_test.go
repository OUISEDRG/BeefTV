package canvasworkflow

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"infinite-canvas/backend/internal/model"
)

type capabilityHost struct {
	CompileHost
	snapshot map[string]any
}

func (h capabilityHost) ProviderConfig() (map[string]any, error) { return h.snapshot, nil }
func (h capabilityHost) LocalMode() bool                         { return false }
func (h capabilityHost) UpscaleAvailable(string) error           { return errors.New("not configured") }

func workflowCapabilityHost(t *testing.T) capabilityHost {
	t.Helper()
	var snapshot map[string]any
	err := json.Unmarshal([]byte(`{"channels":[{"id":"dreama","enabled":true,"baseUrl":"http://127.0.0.1:18765","models":["image"],"modelProfiles":[{"model":"image","capability":"image","protocol":"dreama-images-async"}]},{"id":"codex","enabled":true,"baseUrl":"http://127.0.0.1:18766","models":["text"],"modelProfiles":[{"model":"text","capability":"text","protocol":"openai-response"}]}]}`), &snapshot)
	if err != nil {
		t.Fatal(err)
	}
	return capabilityHost{snapshot: snapshot}
}

func TestWorkflowCatalogOmitsUnboundDreamaAndPreservesTextAndLocalTools(t *testing.T) {
	operations := PresentOperations(workflowCapabilityHost(t))
	seen := map[string]bool{}
	textFound := false
	for _, operation := range operations {
		seen[operation.ID] = true
		for _, field := range operation.Fields {
			for _, option := range field.Options {
				if strings.HasPrefix(option.Value, "dreama::") {
					t.Fatalf("unbound Dreama model advertised: %+v", option)
				}
				if operation.ID == "generate.text" && option.Value == "codex::text" {
					textFound = true
				}
			}
		}
	}
	if !textFound || !seen["image.crop"] || !seen["video.trim"] || !seen["video.depth"] {
		t.Fatalf("supported operations lost: text=%v, operations=%v", textFound, seen)
	}
}

func TestWorkflowSelectionAndStoredRecipeRejectDreamaBeforeExecution(t *testing.T) {
	host := workflowCapabilityHost(t)
	for _, protocol := range []string{"dreama-images-async", "dreama-videos-async", "dreama-extension-async", "dreama-enhancement-async", "dreama-audio-async"} {
		recipe := Recipe{Request: RecipeRequest{Input: map[string]any{"config": map[string]any{"interfaceType": protocol}}}}
		if _, err := SelectModel(host, Node{}, &recipe); err == nil {
			t.Fatalf("stored %s selected", protocol)
		}
		if _, err := Materialize(host, "owner", "operation", recipe, nil, nil); err == nil {
			t.Fatalf("stored %s executed", protocol)
		}
	}
	recipe := Recipe{Request: RecipeRequest{Input: map[string]any{"config": map[string]any{}}}}
	if _, err := SelectModel(host, Node{MediaType: "image", Config: map[string]any{"modelOption": "dreama::image"}}, &recipe); err == nil {
		t.Fatal("new Dreama selection accepted")
	}
	if _, err := SelectModel(host, Node{MediaType: "text", Config: map[string]any{"modelOption": "codex::text"}}, &recipe); err != nil {
		t.Fatalf("text selection rejected: %v", err)
	}
}

type retryCapabilityHost struct{ capabilityHost }

func (retryCapabilityHost) LocalMode() bool                                 { return true }
func (retryCapabilityHost) DecryptSetting(value string) (string, error)     { return value, nil }
func (retryCapabilityHost) PersistedFailureBlocksRetry(string, string) bool { return false }

func TestWorkflowUnacceptedRetryCannotReuseLegacyDreamaRecipe(t *testing.T) {
	recipes := RecipesByNode{"video": {Request: RecipeRequest{Input: map[string]any{"config": map[string]any{"interfaceType": "dreama-extension-async"}}}}}
	encoded, err := json.Marshal(recipes)
	if err != nil {
		t.Fatal(err)
	}
	host := retryCapabilityHost{workflowCapabilityHost(t)}
	run := model.CanvasWorkflowRun{RecipeCipher: string(encoded)}
	task := &model.Task{Type: "canvas_video", Status: model.TaskStatusFailed}
	if _, err := PrepareRetryCredentials(host, run, "video", task); err == nil {
		t.Fatal("legacy recipe without channel identity was allowed to retry")
	}
	task.ProviderRequestID = "existing-provider-job"
	if input, err := PrepareRetryCredentials(host, run, "video", task); err != nil || input != "" {
		t.Fatalf("original accepted job recovery changed: input=%q error=%v", input, err)
	}
}
