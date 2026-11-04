package operations_test

import (
	"fmt"
	"testing"
	"time"

	"infinite-canvas/backend/internal/model"
	localtask "infinite-canvas/backend/internal/task"
)

func TestCanvasTaskBindReplacesRunningOutcomeWithDurableSuccess(t *testing.T) {
	for _, kind := range []string{"video", "image"} {
		t.Run(kind, func(t *testing.T) {
			h := newHarness(t)
			db, err := h.service.Database().DB()
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = db.Close() })
			started := time.Date(2026, 11, 1, 5, 30, 0, 0, time.UTC)
			completed := started.Add(249 * time.Second)
			resource := model.Resource{ID: "res-terminal", UserID: h.userID, Kind: kind, Status: model.ResourceStatusReady,
				MimeType: "video/mp4", Size: 12, Width: 720, Height: 1280, CreatedAt: started, UpdatedAt: completed}
			resultJSON := `{"video":{"resourceId":"res-terminal"}}`
			if kind == "image" {
				resource.MimeType = "image/png"
				resultJSON = `{"images":[{"resourceId":"res-terminal"}]}`
			}
			if err := h.service.Database().Create(&resource).Error; err != nil {
				t.Fatal(err)
			}
			task := model.Task{ID: "task-terminal", UserID: h.userID, ProjectID: h.canvasID, Type: "canvas_" + kind,
				Status: model.TaskStatusSucceeded, InputJSON: `{"metadata":{"source":"canvas","nodeId":"node-terminal"}}`,
				ResultJSON: resultJSON, CreatedAt: started, UpdatedAt: completed, StartedAt: &started, CompletedAt: &completed}
			if err := h.service.Database().Create(&task).Error; err != nil {
				t.Fatal(err)
			}
			// Deliver before the destination exists so this test exercises the bind
			// transaction itself, independently of the background binding scanner.
			if err := h.service.DeliverSucceededTask(task); err != nil {
				t.Fatal(err)
			}
			h.addNode(t, "node-terminal", kind, "Keep title", map[string]any{
				"taskId": task.ID, "status": "loading", "taskStatus": "running", "taskProgress": 98,
				"taskOutcome":   map[string]any{"phase": "running", "regenerate": "blocked", "reason": "task_running"},
				"taskUpdatedAt": started.Format(time.RFC3339Nano), "taskCompletedAt": nil,
				"taskOutcomeSource": "local", "taskErrorCode": "stale", "generationSubmissionState": "accepted",
			})
			if err := h.service.Database().First(&task, "id = ?", task.ID).Error; err != nil {
				t.Fatal(err)
			}
			first, err := h.bindTask(t, task.ID, "node-terminal", 0)
			if err != nil {
				t.Fatal(err)
			}
			meta := nodeMeta(h.node(t, "node-terminal"))
			outcome, _ := meta["taskOutcome"].(map[string]any)
			if outcome["phase"] != "ready" || outcome["reason"] != "result_ready" || outcome["regenerate"] != "allowed" {
				t.Fatalf("successful bind retained nonterminal outcome: %#v", outcome)
			}
			for field, want := range map[string]time.Time{"taskStartedAt": started, "taskCompletedAt": completed, "taskUpdatedAt": task.UpdatedAt} {
				got, err := time.Parse(time.RFC3339Nano, fmt.Sprint(meta[field]))
				if err != nil || !got.Equal(want) {
					t.Errorf("%s = %#v, want %s", field, meta[field], want.Format(time.RFC3339Nano))
				}
			}
			if meta["taskDurationMs"] != float64(249000) || meta["taskOutcomeSource"] != nil || meta["taskErrorCode"] != nil {
				t.Errorf("success did not settle task metadata: duration=%#v source=%#v error=%#v", meta["taskDurationMs"], meta["taskOutcomeSource"], meta["taskErrorCode"])
			}
			replay, err := h.bindTask(t, task.ID, "node-terminal", 0)
			if err != nil || !replay.Replayed || replay.Revision != first.Revision {
				t.Fatalf("same bind must stay idempotent: replay=%#v err=%v", replay, err)
			}
			if h.count(t, &model.Asset{}) != 1 || h.receiptCount(t, localtask.AttachNodeEffectKey(task.ID, "node-terminal", 0)) != 1 {
				t.Fatal("terminal state synchronization duplicated asset or receipt")
			}
		})
	}
}
