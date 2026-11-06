package operations_test

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"sync"
	"testing"
	"time"

	"infinite-canvas/backend/internal/model"
	"infinite-canvas/backend/internal/operations"
	localtask "infinite-canvas/backend/internal/task"

	"gorm.io/gorm"
)

func seedCompositeTask(t *testing.T, h *harness, edit string) (model.Task, string) {
	t.Helper()
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := h.service.SupportTrace().Close(ctx); err != nil {
			t.Errorf("close support trace: %v", err)
		}
	})
	task := h.seedReadyCanvasImageTask(t, "composite-task", "composite-node", "Edited image", 31, 52)
	input, err := json.Marshal(map[string]any{
		"metadata": map[string]any{"source": "canvas", "nodeId": "composite-node", "edit": edit,
			"emotion": map[string]any{"sourceWidth": 800, "sourceHeight": 600}},
		"referenceImages": []any{map[string]any{"storageKey": "resource:composite-source"}},
	})
	if err != nil {
		t.Fatal(err)
	}
	task.InputJSON = string(input)
	if err := h.service.Database().Model(&model.Task{}).Where("id = ?", task.ID).Update("input_json", task.InputJSON).Error; err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"composite-source", "composite-result"} {
		if err := h.service.Database().Create(&model.Resource{ID: id, UserID: h.userID, Kind: "image", Status: model.ResourceStatusReady, MimeType: "image/png", Width: 800, Height: 600, Size: 100}).Error; err != nil {
			t.Fatal(err)
		}
	}
	payload, err := json.Marshal(map[string]any{
		"kind": "image", "data": map[string]any{"storageKey": "resource:composite-result"},
		"metadata": map[string]any{"source": "canvas-generation", "canvasId": h.canvasID, "nodeId": "composite-node", "taskId": task.ID},
	})
	if err != nil {
		t.Fatal(err)
	}
	assetID := "saved-composite"
	if err := h.service.Database().Create(&model.Asset{ID: assetID, UserID: h.userID, Kind: "image", PayloadJSON: string(payload)}).Error; err != nil {
		t.Fatal(err)
	}
	return task, assetID
}

func bindComposite(t *testing.T, h *harness, taskID, assetID string) (operations.Result, error) {
	t.Helper()
	return h.execute(t, operations.ManualCaller(false), "canvas.task.bind", localtask.AttachNodeEffectKey(taskID, "composite-node", 0), map[string]any{
		"canvasId": h.canvasID, "taskId": taskID, "nodeId": "composite-node", "outputIndex": 0, "compositeAssetId": assetID,
	})
}

func TestCanvasCompositeBindPersistsOnlyTheOwnedNodeAndReplays(t *testing.T) {
	for _, edit := range []string{"emotion", "mask-guide"} {
		t.Run(edit, func(t *testing.T) {
			h := newHarness(t)
			task, assetID := seedCompositeTask(t, h, edit)
			otherBefore := h.node(t, "n1")
			nodeBefore := h.node(t, "composite-node")
			beforeRevision := h.revisionNow(t)
			if _, err := h.bindTask(t, task.ID, "composite-node", 0); opErr(t, err).Reason != "local_image_composite_required" {
				t.Fatalf("raw output bind = %v", err)
			}
			if err := h.service.DeliverSucceededTask(task); err != nil {
				t.Fatal(err)
			}
			if h.nodeMetaString(t, "composite-node", "status") != "loading" || h.revisionNow(t) != beforeRevision {
				t.Fatal("delivery worker bound the raw model output")
			}
			first, err := bindComposite(t, h, task.ID, assetID)
			if err != nil {
				t.Fatal(err)
			}
			if first.Replayed || first.Revision != beforeRevision+1 {
				t.Fatalf("first composite bind = %#v", first)
			}
			bound := h.node(t, "composite-node")
			meta := nodeMeta(bound)
			if meta["assetId"] != assetID || meta["storageKey"] != "resource:composite-result" || meta["content"] != "/api/resources/composite-result/file" || meta["status"] != "success" || meta["taskId"] != task.ID {
				t.Fatalf("wrong bound result: %#v", meta)
			}
			if !reflect.DeepEqual(h.node(t, "n1"), otherBefore) || bound["title"] != nodeBefore["title"] || !reflect.DeepEqual(bound["position"], nodeBefore["position"]) {
				t.Fatal("composite bind overwrote user changes or unrelated node")
			}
			if err := h.service.DeliverSucceededTask(task); err != nil {
				t.Fatal(err)
			}
			if h.nodeMetaString(t, "composite-node", "storageKey") != "resource:composite-result" {
				t.Fatal("worker replaced composite with raw output")
			}
			replay, err := bindComposite(t, h, task.ID, assetID)
			if err != nil || !replay.Replayed || replay.Revision != first.Revision {
				t.Fatalf("composite replay = %#v err=%v", replay, err)
			}
			if h.receiptCount(t, localtask.AttachNodeEffectKey(task.ID, "composite-node", 0)) != 1 || h.count(t, &model.Asset{}) != 2 || h.count(t, &model.Task{}) != 1 {
				t.Fatal("bind/replay duplicated receipt, asset or paid task")
			}
		})
	}
}

func TestCanvasCompositeBindRefusesDeletedOrReplacedTargetAndFailedTask(t *testing.T) {
	for _, state := range []string{"deleted", "new task", "failed"} {
		t.Run(state, func(t *testing.T) {
			h := newHarness(t)
			task, assetID := seedCompositeTask(t, h, "emotion")
			reason := "node_deleted"
			switch state {
			case "deleted":
				h.deleteNode(t, "composite-node")
			case "new task":
				node := h.node(t, "composite-node")
				nodeMeta(node)["taskId"] = "newer-task"
				h.replaceNode(t, "composite-node", node)
				reason = "node_task_mismatch"
			case "failed":
				if err := h.service.Database().Model(&model.Task{}).Where("id = ?", task.ID).Update("status", model.TaskStatusFailed).Error; err != nil {
					t.Fatal(err)
				}
				reason = "task_not_succeeded"
			}
			before := h.document(t)
			if _, err := bindComposite(t, h, task.ID, assetID); opErr(t, err).Reason != reason {
				t.Fatalf("got %v, want %s", err, reason)
			}
			if !reflect.DeepEqual(h.document(t), before) || h.receiptCount(t, localtask.AttachNodeEffectKey(task.ID, "composite-node", 0)) != 0 {
				t.Fatal("refused bind changed canvas or created receipt")
			}
			if !h.assetExists(t, assetID) {
				t.Fatal("refused bind discarded completed composite")
			}
		})
	}
}

func TestCanvasCompositeBindReceiptFailureRollsBackAndCanRecover(t *testing.T) {
	h := newHarness(t)
	task, assetID := seedCompositeTask(t, h, "emotion")
	before := h.document(t)
	db := h.service.Database()
	if err := db.Callback().Update().Before("gorm:update").Register("composite_receipt_failure", func(tx *gorm.DB) {
		if tx.Statement.Schema != nil && tx.Statement.Schema.Name == "AgentOpRecord" {
			tx.AddError(errors.New("receipt unavailable"))
		}
	}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = db.Callback().Update().Remove("composite_receipt_failure") })
	if _, err := bindComposite(t, h, task.ID, assetID); err == nil {
		t.Fatal("injected failure did not propagate")
	}
	if !reflect.DeepEqual(h.document(t), before) || h.receiptCount(t, localtask.AttachNodeEffectKey(task.ID, "composite-node", 0)) != 0 || !h.assetExists(t, assetID) {
		t.Fatal("receipt failure did not roll back atomically or lost composite")
	}
	if err := db.Callback().Update().Remove("composite_receipt_failure"); err != nil {
		t.Fatal(err)
	}
	if _, err := bindComposite(t, h, task.ID, assetID); err != nil {
		t.Fatalf("retry existing product failed: %v", err)
	}
	if h.count(t, &model.Task{}) != 1 {
		t.Fatal("recovery created another model task")
	}
}

func TestCanvasCompositeConcurrentConsumersShareOneReceipt(t *testing.T) {
	h := newHarness(t)
	task, assetID := seedCompositeTask(t, h, "emotion")
	before := h.revisionNow(t)
	var wg sync.WaitGroup
	errs := make(chan error, 2)
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); _, err := bindComposite(t, h, task.ID, assetID); errs <- err }()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil && operations.AsError(err).Reason != "operation_in_progress" {
			t.Fatal(err)
		}
	}
	if h.revisionNow(t) != before+1 || h.receiptCount(t, localtask.AttachNodeEffectKey(task.ID, "composite-node", 0)) != 1 || h.count(t, &model.Task{}) != 1 {
		t.Fatal("concurrent recovery duplicated the effect or task")
	}
}
