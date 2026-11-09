package app

import (
	"context"
	"strings"
	"testing"
	"time"

	"infinite-canvas/backend/internal/model"
	"infinite-canvas/backend/internal/repository"

	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

func TestCancelTaskDoesNotCancelDreamaGenerationWithoutUpstreamSupport(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:"+newID()+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&model.Task{}); err != nil {
		t.Fatal(err)
	}
	svc := &Service{repo: repository.New(db)}
	for _, protocol := range []string{"dreama-videos-async", "dreama-images-async", "dreama-extension-async", "dreama-enhancement-async", "dreama-audio-async", "seedance-videos-compatible"} {
		t.Run(protocol, func(t *testing.T) {
			config := providerConfig{InterfaceType: protocol}
			if protocol == "seedance-videos-compatible" {
				config.ChannelID = "dreama-video-enhancement"
			}
			for _, status := range []model.TaskStatus{model.TaskStatusQueued, model.TaskStatusRunning} {
				task := model.Task{ID: newID(), UserID: "local", Status: status,
					InputJSON: mustJSON(canvasGenerationInput{Config: config})}
				if status == model.TaskStatusRunning {
					task.ProviderRequestID = "already-accepted"
				}
				if err := db.Create(&task).Error; err != nil {
					t.Fatal(err)
				}
				if !taskForOutput(task).CancelUnsupported || !taskSummaryForOutput(task).CancelUnsupported {
					t.Fatal("task projection did not expose the unsupported cancellation capability")
				}
				if _, err := svc.CancelTask(context.Background(), task.UserID, task.ID); err == nil || !strings.Contains(err.Error(), "剧梦") {
					t.Fatalf("cancel did not report unavailable for %s: %v", status, err)
				}
				stored, err := svc.repo.TaskForUser(task.UserID, task.ID)
				if err != nil || stored.Status != status || stored.ProviderRequestID != task.ProviderRequestID || stored.CompletedAt != nil {
					t.Fatalf("native task changed on cancellation attempt: %+v %v", stored, err)
				}
			}
		})
	}
}

func TestCancelTaskCancelsRunningTask(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:"+newID()+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&model.Task{}); err != nil {
		t.Fatal(err)
	}
	startedAt := time.Now()
	task := model.Task{
		ID:        "running-task",
		UserID:    "user-1",
		Status:    model.TaskStatusRunning,
		Stage:     "调用生成模型",
		StartedAt: &startedAt,
	}
	if err := db.Create(&task).Error; err != nil {
		t.Fatal(err)
	}

	svc := &Service{repo: repository.New(db)}
	cancelled, err := svc.CancelTask(context.Background(), task.UserID, task.ID)
	if err != nil {
		t.Fatalf("CancelTask() error = %v", err)
	}
	if cancelled.Status != model.TaskStatusCancelled {
		t.Fatalf("CancelTask() status = %s, want cancelled", cancelled.Status)
	}

	stored, err := svc.repo.TaskForUser(task.UserID, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if stored.Status != model.TaskStatusCancelled || stored.CompletedAt == nil {
		t.Fatalf("running task was not cancelled: status=%s completedAt=%v", stored.Status, stored.CompletedAt)
	}
}

func TestCancelTaskCancelsQueuedTask(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:"+newID()+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&model.Task{}); err != nil {
		t.Fatal(err)
	}
	task := model.Task{ID: "queued-task", UserID: "user-1", Status: model.TaskStatusQueued, Stage: "等待队列调度"}
	if err := db.Create(&task).Error; err != nil {
		t.Fatal(err)
	}

	svc := &Service{repo: repository.New(db)}
	cancelled, err := svc.CancelTask(context.Background(), task.UserID, task.ID)
	if err != nil {
		t.Fatalf("CancelTask() error = %v", err)
	}
	if cancelled.Status != model.TaskStatusCancelled {
		t.Fatalf("CancelTask() status = %s, want cancelled", cancelled.Status)
	}

	storedTask, err := svc.repo.TaskForUser(task.UserID, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if storedTask.Status != model.TaskStatusCancelled || storedTask.CompletedAt == nil {
		t.Fatalf("queued task was not cancelled: status=%s completedAt=%v", storedTask.Status, storedTask.CompletedAt)
	}
}

func TestCancelTaskLocalModeCancelsQueuedTask(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:"+newID()+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&model.Task{}); err != nil {
		t.Fatal(err)
	}
	task := model.Task{ID: "local-queued-task", UserID: "local-user", Status: model.TaskStatusQueued}
	if err := db.Create(&task).Error; err != nil {
		t.Fatal(err)
	}

	svc := &Service{
		repo: repository.New(db),
	}
	if _, err := svc.CancelTask(context.Background(), task.UserID, task.ID); err != nil {
		t.Fatalf("CancelTask() error = %v", err)
	}
}

func TestCancelTaskRejectsTaskWithProviderRequestID(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:"+newID()+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&model.Task{}); err != nil {
		t.Fatal(err)
	}
	task := model.Task{
		ID:                "submitted-task",
		UserID:            "user-1",
		Status:            model.TaskStatusFailed,
		ProviderRequestID: "provider-request-1",
	}
	if err := db.Create(&task).Error; err != nil {
		t.Fatal(err)
	}

	svc := &Service{repo: repository.New(db)}
	if _, err := svc.CancelTask(context.Background(), task.UserID, task.ID); err == nil || err.Error() != "任务当前状态为 failed，无法取消" {
		t.Fatalf("CancelTask() error = %v", err)
	}

	stored, err := svc.repo.TaskForUser(task.UserID, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if stored.Status != model.TaskStatusFailed || stored.ProviderRequestID != task.ProviderRequestID {
		t.Fatalf("submitted task changed after cancellation attempt: status=%s providerRequestId=%q", stored.Status, stored.ProviderRequestID)
	}
}
