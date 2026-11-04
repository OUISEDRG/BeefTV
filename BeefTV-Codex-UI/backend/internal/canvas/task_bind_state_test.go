package canvas

import (
	"encoding/json"
	"testing"
	"time"

	"infinite-canvas/backend/internal/model"
)

func TestGenerationBindStateRecognizesPersistedTerminalMetadata(t *testing.T) {
	started := time.Date(2026, 11, 1, 5, 30, 0, 0, time.UTC)
	completed := started.Add(time.Minute)
	patch := TaskOutputBind{
		TaskID: "same-task", Content: "existing-result", EffectKey: "same-effect",
		TaskOutcome:   &model.TaskOutcome{Phase: "ready", Regenerate: "allowed", Reason: "result_ready"},
		TaskStartedAt: &started, TaskCompletedAt: &completed, TaskUpdatedAt: completed,
	}
	metadata := map[string]any{"taskId": "same-task", "prompt": "keep verbatim", "taskOutcome": map[string]any{"phase": "running"}}
	applyGenerationBindMetadata(metadata, patch)
	encoded, err := json.Marshal(metadata)
	if err != nil {
		t.Fatal(err)
	}
	var persisted map[string]any
	if err := json.Unmarshal(encoded, &persisted); err != nil {
		t.Fatal(err)
	}
	if !alreadyBound(persisted, patch) {
		t.Fatal("a persisted terminal binding must not advance the canvas revision again")
	}
	if persisted["prompt"] != "keep verbatim" {
		t.Fatal("binding changed the user's prompt")
	}
	persisted["taskStatus"] = "running"
	if alreadyBound(persisted, patch) {
		t.Fatal("same media with an obsolete task status must still settle its task state")
	}
	persisted["taskStatus"] = "succeeded"
	persisted["taskOutcome"] = map[string]any{"phase": "running", "regenerate": "blocked", "reason": "task_running"}
	if alreadyBound(persisted, patch) {
		t.Fatal("same media with an obsolete running outcome must still settle its task state")
	}
}

func TestGenerationBindStateDoesNotInventMissingTaskTimes(t *testing.T) {
	patch := TaskOutputBind{TaskID: "same-task", Content: "ready", TaskOutcome: &model.TaskOutcome{Phase: "ready", Regenerate: "allowed", Reason: "result_ready"}}
	metadata := map[string]any{"taskCompletedAt": "old", "taskUpdatedAt": "old", "taskDurationMs": 123}
	applyGenerationBindMetadata(metadata, patch)
	encoded, err := json.Marshal(metadata)
	if err != nil {
		t.Fatal(err)
	}
	var persisted map[string]any
	if err := json.Unmarshal(encoded, &persisted); err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"taskStartedAt", "taskCompletedAt", "taskUpdatedAt", "taskDurationMs"} {
		if persisted[field] != nil {
			t.Errorf("unknown %s was invented or retained: %#v", field, persisted[field])
		}
	}
}

func TestGenerationBindStateCannotBeSuppliedByJSON(t *testing.T) {
	var patch TaskOutputBind
	if err := json.Unmarshal([]byte(`{"TaskOutcome":{"phase":"ready"},"TaskStartedAt":"2026-11-01T05:30:00Z","TaskCompletedAt":"2026-11-01T05:31:00Z","TaskUpdatedAt":"2026-11-01T05:31:00Z"}`), &patch); err != nil {
		t.Fatal(err)
	}
	if patch.TaskOutcome != nil || patch.TaskStartedAt != nil || patch.TaskCompletedAt != nil || !patch.TaskUpdatedAt.IsZero() {
		t.Fatal("trusted task state accepted from JSON")
	}
}
