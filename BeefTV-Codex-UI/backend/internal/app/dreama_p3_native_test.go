package app

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"sync/atomic"
	"testing"
	"time"

	"infinite-canvas/backend/internal/generation"
	"infinite-canvas/backend/internal/model"
)

func TestP3DreamaNativeRestartAndBinding(t *testing.T) {
	pluginPath := os.Getenv("BEEFTV_P3_DREAMA_PLUGIN")
	if pluginPath == "" {
		t.Skip("P3 harness supplies the archived Dreama release package")
	}
	plugin := readVideoUpgradeFixture(t, pluginPath)
	allowLoopbackProviderTest(t)
	for _, tc := range []struct {
		name, mode, prefix, path, mime string
	}{
		{"videos", "video", "", "/v1/videos", "video/mp4"},
		{"extension", "video", "/extension", "/v1/videos", "video/mp4"},
		{"enhancement", "video", "/enhancement", "/v1/videos", "video/mp4"},
		{"images", "image", "/image", "/v1/images", "image/png"},
		{"audio", "audio", "/audio", "/v1/audio/tasks", "audio/mpeg"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			const (
				user     = "local"
				canvasID = "p3-canvas"
				nodeID   = "p3-node"
				receipt  = "p3-original-receipt"
				prompt   = "  原文\n保留  "
			)
			protocolID := "dreama-" + tc.name + "-async"
			binding := map[string]any{"schemaVersion": 1, "canvasId": canvasID, "nodeId": nodeID, "projectId": 41, "seriesId": 11, "promptMode": "drama", "aspectRatio": "16:9", "bindingRevision": 1}
			canvasBinding := map[string]any{"schemaVersion": 1, "projectId": 41, "projectName": "fixture", "promptMode": "drama", "aspectRatio": "16:9", "defaultSeriesId": 11, "revision": 1}
			var artifact []byte
			switch tc.mode {
			case "image":
				artifact, _ = base64.StdEncoding.DecodeString("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9l8AAAAASUVORK5CYII=")
			case "audio":
				artifact = readVideoUpgradeFixture(t, "testdata/managed-audio-fixture.mp3")
			default:
				artifact = readVideoUpgradeFixture(t, "testdata/qa-grok-imagine-720square.mp4")
			}
			var creates, polls, downloads atomic.Int32
			var upstream *httptest.Server
			upstream = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				switch {
				case r.Method == http.MethodPost && r.URL.Path == tc.prefix+tc.path:
					creates.Add(1)
					var body map[string]any
					if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
						t.Errorf("provider request: %v", err)
					}
					var want map[string]any
					_ = json.Unmarshal([]byte(mustJSON(binding)), &want)
					if !reflect.DeepEqual(body["dreama"], want) || r.Header.Get("Idempotency-Key") == "" {
						t.Errorf("provider request lost binding or idempotency: %v", body["dreama"])
					}
					promptKey := "prompt"
					if tc.mode == "audio" {
						promptKey = "input"
					}
					if tc.name != "enhancement" && body[promptKey] != prompt {
						t.Errorf("provider prompt = %#v", body[promptKey])
					}
					_ = json.NewEncoder(w).Encode(map[string]any{"id": receipt, "status": "pending"})
				case r.Method == http.MethodGet && r.URL.Path == tc.prefix+tc.path+"/"+receipt:
					polls.Add(1)
					result := map[string]any{"id": receipt, "status": "succeeded"}
					mediaURL := upstream.URL + "/artifact"
					switch tc.mode {
					case "image":
						result["images"] = []map[string]string{{"url": mediaURL}}
					case "audio":
						result["audio_url"] = mediaURL
					default:
						result["video_url"] = mediaURL
					}
					_ = json.NewEncoder(w).Encode(result)
				case r.Method == http.MethodGet && r.URL.Path == "/artifact":
					downloads.Add(1)
					w.Header().Set("Content-Type", tc.mime)
					_, _ = w.Write(artifact)
				default:
					t.Errorf("unexpected provider call %s %s", r.Method, r.URL.Path)
					http.NotFound(w, r)
				}
			}))
			defer upstream.Close()

			dir := t.TempDir()
			svc, db := openDBRuntimeMatrix(t, dir)
			defer func() {
				if svc != nil {
					closeDBRuntimeMatrix(t, svc, db)
				}
			}()
			if _, err := svc.InstallPlugin(plugin, "dreama-modalities.beeftv-plugin"); err != nil {
				t.Fatal(err)
			}
			doc := map[string]any{"id": canvasID, "title": "P3 fixture", "revision": 0, "integrations": map[string]any{"dreama": canvasBinding},
				"nodes": []any{map[string]any{"id": nodeID, "type": tc.mode, "title": "fixture", "metadata": map[string]any{}}}, "connections": []any{}}
			if _, err := svc.UpsertUserCanvasProject(user, []byte(mustJSON(doc))); err != nil {
				t.Fatal(err)
			}
			input := canvasGenerationInput{
				Mode: tc.mode, Prompt: prompt, Config: providerConfig{BaseURL: upstream.URL + tc.prefix, InterfaceType: protocolID, APIKey: "fixture-key", Model: "fixture-model",
					Size: "16:9", Quality: "3K", VQuality: "720p", VideoSeconds: "5", Count: "1", AudioFormat: "mp3", AudioVoice: "fixture-voice"},
				Metadata: map[string]any{"source": "canvas", "nodeId": nodeID, "clientOperationId": "p3-stable-operation",
					"providerOptions": map[string]any{"dreama": binding}},
			}
			if tc.name == "extension" || tc.name == "enhancement" {
				input.ReferenceVideos = []providerMedia{{URL: upstream.URL + "/source.mp4"}}
			}
			var payload map[string]any
			_ = json.Unmarshal([]byte(mustJSON(input)), &payload)
			request := CreateTaskRequest{ProjectID: canvasID, Type: "canvas_" + tc.mode, Prompt: prompt, Input: payload}
			task, err := svc.CreateTask(user, request)
			if err != nil {
				t.Fatal(err)
			}
			replay, err := svc.CreateTask(user, request)
			if err != nil || replay.ID != task.ID {
				t.Fatalf("replay did not return original task: %+v %v", replay, err)
			}
			setCanvasBindTestNodeTaskID(t, svc, canvasID, nodeID, task.ID)
			claimed, err := svc.repo.ClaimNextTask("before-restart", 0)
			if err != nil || claimed == nil || claimed.ID != task.ID {
				t.Fatalf("claim: %+v %v", claimed, err)
			}
			storedInput, err := svc.decryptTaskInputJSON(claimed.InputJSON)
			if err != nil {
				t.Fatal(err)
			}
			var providerInput canvasGenerationInput
			if err := json.Unmarshal([]byte(storedInput), &providerInput); err != nil {
				t.Fatal(err)
			}
			ctx := withProviderAnalytics(context.Background(), svc, *claimed)
			ctx = withProtocolRegistry(ctx, svc.protocolRegistry())
			adapter, ok := declarativeProtocolAdapterForContext(ctx, protocolID)
			if !ok {
				t.Fatal("installed provider unavailable")
			}
			ctx = generation.WithSubmissionKey(ctx, task.ID)
			policy := fastVideoPollPolicy()
			policy.Sleep = func(context.Context, time.Duration) error { return context.Canceled }
			_, err = runProtocolAdapterTaskWithPolicy(ctx, providerInput, adapter, policy)
			if !errors.Is(err, context.Canceled) || polls.Load() != 0 {
				t.Fatalf("did not stop after provider acceptance: %v, polls=%d", err, polls.Load())
			}
			saved, err := svc.repo.Task(task.ID)
			if err != nil || saved.ProviderRequestID != receipt {
				t.Fatalf("receipt not stored before restart: %+v %v", saved, err)
			}
			closeDBRuntimeMatrix(t, svc, db)
			svc = nil

			svc, db = openDBRuntimeMatrix(t, dir)
			// Trace storage is independent of delivery and can race Windows temp cleanup.
			svc.closeSupportTrace()
			if err := db.Model(&model.Task{}).Where("id = ?", task.ID).Update("next_poll_at", nil).Error; err != nil {
				t.Fatal(err)
			}
			if err := svc.ProcessNextTask(); err != nil {
				t.Fatal(err)
			}
			assertVideoUpgradeResult(t, svc, dir, task.ID, receipt, artifact)
			final, err := svc.repo.Task(task.ID)
			if err != nil {
				t.Fatal(err)
			}
			if err := svc.recoverCanvasTaskBindings(*final); err != nil {
				t.Fatal(err)
			}
			projected, err := svc.taskDomain().Get(user, task.ID)
			if err != nil {
				t.Fatal(err)
			}
			meta := canvasBindTestNodeMetadata(t, svc, canvasID, nodeID)
			if meta["taskId"] != task.ID || meta["content"] != "/api/resources/"+projected.Outputs[0].ResourceID+"/file" {
				t.Fatalf("node not bound to original delivery: %#v", meta)
			}
			if creates.Load() != 1 || polls.Load() != 1 || downloads.Load() != 1 {
				t.Fatalf("provider call counts create=%d poll=%d download=%d", creates.Load(), polls.Load(), downloads.Load())
			}
		})
	}
}

func TestP3NativeCodexImageCancellation(t *testing.T) {
	pluginPath := os.Getenv("BEEFTV_P3_CODEX_PLUGIN")
	if pluginPath == "" {
		t.Skip("P3 harness supplies the archived Codex image package")
	}
	allowLoopbackProviderTest(t)
	var deletes, polls atomic.Int32
	const receipt = "codex-original-job"
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/images/jobs/"+receipt || r.Header.Get("Authorization") != "Bearer fixture-key" {
			t.Errorf("unexpected cancellation request %s %s", r.Method, r.URL.Path)
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		switch r.Method {
		case http.MethodDelete:
			deletes.Add(1)
			_, _ = w.Write([]byte(`{"id":"codex-original-job","status":"cancelled"}`))
		case http.MethodGet:
			switch polls.Add(1) {
			case 1:
				_, _ = w.Write([]byte(`{"id":"another-job","status":"cancelled"}`))
			case 2:
				_, _ = w.Write([]byte(`{"status":"cancelled"}`))
			default:
				_, _ = w.Write([]byte(`{"id":"codex-original-job","status":"cancelled"}`))
			}
		default:
			t.Errorf("cancellation unexpectedly sent %s", r.Method)
			http.NotFound(w, r)
		}
	}))
	defer upstream.Close()
	svc, db := openDBRuntimeMatrix(t, t.TempDir())
	defer closeDBRuntimeMatrix(t, svc, db)
	if _, err := svc.InstallPlugin(readVideoUpgradeFixture(t, pluginPath), "codex-images.beeftv-plugin"); err != nil {
		t.Fatal(err)
	}
	task := model.Task{
		ID: newID(), UserID: "local", Type: "canvas_image", Status: model.TaskStatusRunning, ProviderRequestID: receipt,
		InputJSON: mustJSON(canvasGenerationInput{Mode: "image", Config: providerConfig{InterfaceType: "codex-image", BaseURL: upstream.URL, APIKey: "fixture-key", Model: "fixture-model"}}),
	}
	if err := db.Create(&task).Error; err != nil {
		t.Fatal(err)
	}
	cancelled, err := svc.CancelTask(context.Background(), task.UserID, task.ID)
	if err != nil || cancelled.Status != model.TaskStatusCancelled || cancelled.CancelUnsupported {
		t.Fatalf("native cancellation: %+v %v", cancelled, err)
	}
	var stored *model.Task
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); {
		stored, err = svc.repo.Task(task.ID)
		if err != nil {
			t.Fatal(err)
		}
		if deletes.Load() > 0 || stored.ProviderCancelStatus == model.ProviderCancelStatusUncertain {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if deletes.Load() != 1 || stored.ProviderCancelStatus != model.ProviderCancelStatusRequested || stored.ProviderRequestID != receipt {
		t.Fatalf("cancel not sent through installed plugin or incorrectly confirmed: deletes=%d state=%s receipt=%s", deletes.Load(), stored.ProviderCancelStatus, stored.ProviderRequestID)
	}
	for i := 0; i < 2; i++ {
		if err := svc.reconcileProviderCancellation(context.Background(), stored); err != nil {
			t.Fatal(err)
		}
		stored, err = svc.repo.Task(task.ID)
		if err != nil || stored.ProviderRequestID != receipt || stored.ProviderCancelStatus != model.ProviderCancelStatusRequested || stored.ProviderCancelledAt != nil {
			t.Fatalf("confirmation without original receipt: %+v err=%v", stored, err)
		}
	}
	if err := svc.reconcileProviderCancellation(context.Background(), stored); err != nil {
		t.Fatal(err)
	}
	stored, err = svc.repo.Task(task.ID)
	if err != nil || stored.ProviderCancelStatus != model.ProviderCancelStatusConfirmed || stored.ProviderCancelledAt == nil || polls.Load() != 3 {
		t.Fatalf("confirmation: %+v polls=%d err=%v", stored, polls.Load(), err)
	}
}
