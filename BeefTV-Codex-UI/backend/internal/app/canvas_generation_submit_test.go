package app

import (
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"

	"infinite-canvas/backend/internal/model"
	"infinite-canvas/backend/internal/operations"
)

// The release check runs this binary alongside its packaged Node/compiler.
// It uses a temporary workspace without starting any task worker or provider.
func TestPreparedCanvasSubmissionWithPackagedCompiler(t *testing.T) {
	path := os.Getenv("BEEFTV_PREPARED_FIXTURE")
	if path == "" {
		t.Skip("release integration requires packaged compiler and synthetic fixture")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Canvas  map[string]any
		Config  json.RawMessage
		NodeIDs []string
	}
	if err = json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	svc, canvasID, _ := newAssistantTurnService(t)
	db, _ := svc.Database().DB()
	db.SetMaxOpenConns(1)
	t.Cleanup(func() { _ = db.Close() })
	if err = svc.SaveLocalModelConfig(fixture.Config); err != nil {
		t.Fatal(err)
	}
	fixture.Canvas["id"] = canvasID
	fixture.Canvas["revision"] = canvasRevisionOf(t, svc, canvasID)
	for _, id := range []string{"person", "scene"} {
		if err = svc.Database().Create(&model.Resource{ID: id, UserID: "local", Status: model.ResourceStatusReady, MimeType: "image/png", Width: 100, Height: 100}).Error; err != nil {
			t.Fatal(err)
		}
		if err = svc.Database().Create(&model.Asset{ID: id, UserID: "local", Kind: "image", PayloadJSON: `{"resourceId":"` + id + `"}`}).Error; err != nil {
			t.Fatal(err)
		}
	}
	for _, value := range fixture.Canvas["nodes"].([]any) {
		node := value.(map[string]any)
		if node["type"] == "image" {
			node["metadata"].(map[string]any)["assetId"] = node["id"]
		}
	}
	raw, _ = json.Marshal(fixture.Canvas)
	if _, err = svc.UpsertUserCanvasProject("local", raw); err != nil {
		t.Fatal(err)
	}
	registry := operations.NewRegistry(svc, operations.NewStore(svc.Database()))
	operations.RegisterDefaultOps(registry)
	raw, _ = json.Marshal(map[string]any{"canvasId": canvasID, "nodeIds": fixture.NodeIDs})
	preview, err := registry.Execute(operations.Request{Op: "canvas.generation.prepare", UserID: "local", Caller: operations.ExternalCaller(true), Params: raw})
	if err != nil {
		t.Fatal(err)
	}
	encoded, _ := json.Marshal(preview.Result)
	var prepared map[string]any
	json.Unmarshal(encoded, &prepared)
	params := map[string]any{"canvasId": canvasID, "nodeIds": fixture.NodeIDs, "expectedRevision": prepared["canvasRevision"], "expectedModelConfigRevision": prepared["modelConfigRevision"], "expectedSnapshotHash": strings.Repeat("0", 64)}
	raw, _ = json.Marshal(params)
	req := operations.Request{Op: "canvas.generation.submit", OpID: "approved-once", UserID: "local", Caller: operations.ExternalCaller(false), Params: raw}
	if _, err = registry.Execute(req); err == nil || operations.AsError(err).Reason != "generation_snapshot_changed" {
		t.Fatal("changed snapshot admitted", err)
	}
	params["expectedSnapshotHash"] = prepared["snapshotHash"]
	req.Params, _ = json.Marshal(params)
	first, err := registry.Execute(req)
	if err != nil {
		t.Fatal(err)
	}
	second, err := registry.Execute(req)
	if err != nil || !second.Replayed || !reflect.DeepEqual(first.Result, second.Result) {
		t.Fatal("unstable replay", err)
	}
	var tasks []model.Task
	svc.Database().Find(&tasks)
	if len(tasks) != 1 || tasks[0].Status != model.TaskStatusQueued {
		t.Fatal("expected one queued task and zero executions", len(tasks))
	}
	var input struct {
		Prompt          string
		ReferenceImages []struct{ ID string }
		Metadata        map[string]any
		Config          map[string]any
	}
	json.Unmarshal([]byte(tasks[0].InputJSON), &input)
	if input.Prompt != "  @图片1 说话。\n@图片2，@图片1 再说。  " || len(input.ReferenceImages) != 2 || input.ReferenceImages[0].ID != "person" || input.ReferenceImages[1].ID != "scene" {
		t.Fatalf("lost final references: %+v", input.ReferenceImages)
	}
	options := input.Metadata["providerOptions"].(map[string]any)
	if options["dreama"].(map[string]any)["seriesId"] != float64(12) {
		t.Fatal("lost episode")
	}
	voices := options["dreama-videos-async"].(map[string]any)["character_voices"].([]any)
	voice := voices[0].(map[string]any)
	if len(voices) != 1 || voice["image_index"] != float64(1) || voice["voice_id"] != "voice-2" {
		t.Fatal("voice no longer follows final image order")
	}
	if input.Config["videoSeconds"] != float64(5) || input.Config["vquality"] != "720" {
		t.Fatal("compiled options changed")
	}
	raw, _ = svc.UserCanvasProject("local", canvasID)
	var doc map[string]any
	json.Unmarshal(raw, &doc)
	params["expectedRevision"] = doc["revision"]
	params["expectedSnapshotHash"] = prepared["snapshotHash"]
	// A new caller key cannot generate again, even after preparing the new revision.
	raw, _ = json.Marshal(map[string]any{"canvasId": canvasID, "nodeIds": fixture.NodeIDs})
	preview, err = registry.Execute(operations.Request{Op: "canvas.generation.prepare", UserID: "local", Caller: operations.ExternalCaller(true), Params: raw})
	if err != nil {
		t.Fatal(err)
	}
	encoded, _ = json.Marshal(preview.Result)
	json.Unmarshal(encoded, &prepared)
	params["expectedSnapshotHash"] = prepared["snapshotHash"]
	req.Params, _ = json.Marshal(params)
	req.OpID = "new-key"
	if _, err = registry.Execute(req); err == nil || operations.AsError(err).Reason != "generation_task_exists" {
		t.Fatal("duplicate generation accepted", err)
	}
}
