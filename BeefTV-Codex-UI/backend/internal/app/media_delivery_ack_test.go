package app

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"image/png"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"sync/atomic"
	"testing"
	"time"

	"infinite-canvas/backend/internal/generation"
	"infinite-canvas/backend/internal/model"
	"infinite-canvas/backend/internal/protocol"
)

// Uses a real loopback Bridge from the isolated P5 harness and a fake generator.
func TestP5NativeBridgeWire(t *testing.T) {
	baseURL := os.Getenv("BEEFTV_P5_WIRE_URL")
	if baseURL == "" {
		t.Skip("P5 wire harness supplies an isolated local Bridge")
	}
	allowLoopbackProviderTest(t)
	const user, canvasID, token = "local", "p5-wire-canvas", "p5-local-test-token"
	const conversationID, messageID = "p5-wire-conversation", "p5-wire-message"
	manifest, adapter, providerModel, count := os.Getenv("BEEFTV_P5_CODEX_MANIFEST"), "codex-image", "codex-image", 1
	quality := "1k"
	dreama := os.Getenv("BEEFTV_P5_WIRE_BRIDGE") == "dreama"
	if dreama {
		manifest, adapter, providerModel, count = os.Getenv("BEEFTV_P5_DREAMA_MANIFEST"), "dreama-images-async", "7fa38b0a-d222-4a8b-a605-7b55a8bc807a", 2
		quality = "3K"
	}
	dir := t.TempDir()
	svc, db := openDBRuntimeMatrix(t, dir)
	defer func() {
		if svc != nil {
			closeDBRuntimeMatrix(t, svc, db)
		}
	}()
	svc.closeSupportTrace()
	if _, err := svc.InstallPlugin(testPluginPackage(t, readVideoUpgradeFixture(t, manifest)), "p5-wire.beeftv-plugin"); err != nil {
		t.Fatal(err)
	}
	nodes := make([]map[string]any, count)
	nodeIDs := make([]string, count)
	for index := range count {
		nodeIDs[index] = fmt.Sprintf("p5-wire-node-%d", index)
		nodes[index] = map[string]any{"id": nodeIDs[index], "type": "image", "metadata": map[string]any{"taskId": "waiting"}}
	}
	doc := map[string]any{"id": canvasID, "title": "P5 wire", "revision": 0, "nodes": nodes, "connections": []any{}}
	providerURL := baseURL
	if dreama {
		providerURL += "/image"
		doc["integrations"] = map[string]any{"dreama": map[string]any{"schemaVersion": 1,
			"projectId": 41, "projectName": "P5 fixture", "promptMode": "drama", "aspectRatio": "1:1",
			"defaultSeriesId": 11, "revision": 1}}
	}
	if _, err := svc.UpsertUserCanvasProject(user, []byte(mustJSON(doc))); err != nil {
		t.Fatal(err)
	}
	metadata := map[string]any{"source": "canvas", "nodeId": nodeIDs[0], "outputNodeIds": nodeIDs,
		"conversationId": conversationID, "messageId": messageID}
	if dreama {
		metadata["providerOptions"] = map[string]any{"dreama": map[string]any{"schemaVersion": 1,
			"canvasId": canvasID, "nodeId": nodeIDs[0], "projectId": 41, "seriesId": 11,
			"promptMode": "drama", "aspectRatio": "1:1", "bindingRevision": 1}}
	}
	task, err := svc.CreateTask(user, CreateTaskRequest{ProjectID: canvasID, Type: "canvas_image", Prompt: "P5 wire fixture",
		Input: map[string]any{"mode": "image", "prompt": "P5 wire fixture", "metadata": metadata,
			"config": map[string]any{"interfaceType": adapter, "model": providerModel, "baseUrl": providerURL,
				"apiKey": token, "quality": quality, "size": "1:1", "count": "1"}}})
	if err != nil {
		t.Fatal(err)
	}
	conversation := model.CreationConversation{UserID: user, ConversationID: conversationID, Revision: 1,
		Document: mustJSON(map[string]any{"id": conversationID, "title": "P5 wire",
			"messages": []map[string]any{{"id": messageID, "role": "assistant", "taskIds": []string{task.ID}}}})}
	if err := db.Create(&conversation).Error; err != nil {
		t.Fatal(err)
	}
	for _, nodeID := range nodeIDs {
		setCanvasBindTestNodeTaskID(t, svc, canvasID, nodeID, task.ID)
	}
	if err := svc.ProcessNextTask(); err != nil {
		t.Fatal(err)
	}
	stored, err := svc.repo.Task(task.ID)
	if err != nil || stored.Status != model.TaskStatusSucceeded {
		t.Fatalf("wire task not delivered: %+v %v", stored, err)
	}
	if err := svc.recoverCanvasTaskBindings(*stored); err != nil {
		t.Fatal(err)
	}
	if err := svc.mediaAcks().Process(context.Background()); err != nil {
		t.Fatal(err)
	}
	var before []model.MediaDeliveryAck
	if err := db.Where("task_id = ?", task.ID).Order("output_index").Find(&before).Error; err != nil ||
		len(before) != count {
		t.Fatalf("original ACK loss was not persisted: %+v %v", before, err)
	}
	var failedIndex = -1
	for index, row := range before {
		if row.Attempts != 1 || (row.ConfirmedAt == nil && (row.LastError != "provider_ack_retry" || failedIndex >= 0)) {
			t.Fatalf("unexpected ACK attempt before restart: %+v", row)
		}
		if row.ConfirmedAt == nil {
			failedIndex = index
		}
	}
	if failedIndex < 0 {
		t.Fatal("no lost response was recorded")
	}
	ackPath := "/v1/images/jobs/"
	if dreama {
		ackPath = "/v1/jobs/"
	}
	req, err := http.NewRequest(http.MethodGet, baseURL+ackPath+stored.ProviderRequestID+"/ack", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	res, err := (&http.Client{Timeout: 3 * time.Second}).Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var bridge struct {
		DeliveryReceipts []struct {
			Ack protocol.MediaAck `json:"ack"`
		} `json:"deliveryReceipts"`
	}
	if err := json.NewDecoder(res.Body).Decode(&bridge); err != nil || res.StatusCode != 200 || len(bridge.DeliveryReceipts) != count {
		res.Body.Close()
		t.Fatalf("Bridge did not persist the original ACK: status=%d receipts=%+v error=%v", res.StatusCode, bridge, err)
	}
	res.Body.Close()
	closeDBRuntimeMatrix(t, svc, db)
	svc = nil
	svc, db = openDBRuntimeMatrix(t, dir)
	svc.closeSupportTrace()
	if err := db.Model(&model.MediaDeliveryAck{}).Where("task_id = ?", task.ID).Update("next_attempt_at", time.Now().UTC().Add(-time.Minute)).Error; err != nil {
		t.Fatal(err)
	}
	if err := svc.mediaAcks().Process(context.Background()); err != nil {
		t.Fatal(err)
	}
	var after []model.MediaDeliveryAck
	if err := db.Where("task_id = ?", task.ID).Order("output_index").Find(&after).Error; err != nil || len(after) != count {
		t.Fatalf("recovered acknowledgements: %+v %v", after, err)
	}
	var resources []string
	conversationRow, err := svc.repo.CreationConversation(nil, user, conversationID)
	var boundConversation struct {
		Messages []struct {
			ID         string   `json:"id"`
			ResultURLs []string `json:"resultUrls"`
		} `json:"messages"`
	}
	if err != nil || json.Unmarshal([]byte(conversationRow.Document), &boundConversation) != nil ||
		len(boundConversation.Messages) != 1 || boundConversation.Messages[0].ID != messageID {
		t.Fatalf("conversation delivery missing: %+v %v", boundConversation, err)
	}
	for index, row := range after {
		var ack protocol.MediaAck
		if err := json.Unmarshal([]byte(row.PayloadJSON), &ack); err != nil || row.ConfirmedAt == nil ||
			row.PayloadJSON != before[index].PayloadJSON || row.IdempotencyKey != before[index].IdempotencyKey ||
			ack.ArtifactID != bridge.DeliveryReceipts[index].Ack.ArtifactID ||
			ack.ResourceID != bridge.DeliveryReceipts[index].Ack.ResourceID ||
			ack.OutputIndex != index || slices.Contains(resources, ack.ResourceID) {
			t.Fatalf("output %d was not durably delivered and replayed: %+v %v", index, row, err)
		}
		resources = append(resources, ack.ResourceID)
		if !slices.Contains(boundConversation.Messages[0].ResultURLs, "/api/resources/"+ack.ResourceID+"/file") {
			t.Fatalf("output %d did not persist a matching conversation result", index)
		}
		resource, body, err := svc.OpenResource(user, ack.ResourceID)
		if err != nil {
			t.Fatal(err)
		}
		data, err := io.ReadAll(body)
		body.Close()
		if err != nil || resource.Size != ack.SizeBytes || fmt.Sprintf("%x", sha256.Sum256(data)) != ack.SHA256 {
			t.Fatalf("saved original resource does not match ACK: %+v %v", resource, err)
		}
		metadata := canvasBindTestNodeMetadata(t, svc, canvasID, nodeIDs[index])
		if metadata["taskId"] != task.ID || metadata["content"] != "/api/resources/"+ack.ResourceID+"/file" {
			t.Fatalf("reopened canvas output %d did not bind the original resource: %#v", index, metadata)
		}
		image, err := png.Decode(bytes.NewReader(data))
		if err != nil {
			t.Fatal(err)
		}
		red, green, blue, alpha := image.At(0, 0).RGBA()
		if green != 0 || alpha != 0xffff ||
			(index == 0 && (red != 0xffff || blue != 0)) ||
			(index == 1 && (red != 0 || blue != 0xffff)) {
			t.Fatalf("output %d did not preserve fixture order: %04x %04x %04x %04x", index, red, green, blue, alpha)
		}
	}
	if after[failedIndex].Attempts != 2 {
		t.Fatalf("lost ACK must be retried exactly once: %+v", after[failedIndex])
	}
}

func TestP5MismatchedPollCannotReplacePersistedReceipt(t *testing.T) {
	manifestPath := os.Getenv("BEEFTV_P5_CODEX_MANIFEST")
	if manifestPath == "" {
		t.Skip("P5 harness supplies the Codex v3 plugin manifest")
	}
	allowLoopbackProviderTest(t)
	var polls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/v1/images/jobs":
			_, _ = w.Write([]byte(`{"id":"original-job","status":"queued"}`))
		case r.Method == http.MethodGet && r.URL.Path == "/v1/images/jobs/original-job":
			polls.Add(1)
			_, _ = w.Write([]byte(`{"id":"foreign-job","status":"running"}`))
		default:
			t.Errorf("unexpected provider request: %s %s", r.Method, r.URL.Path)
			http.NotFound(w, r)
		}
	}))
	defer upstream.Close()
	svc, db := openDBRuntimeMatrix(t, t.TempDir())
	defer closeDBRuntimeMatrix(t, svc, db)
	svc.closeSupportTrace()
	if _, err := svc.InstallPlugin(testPluginPackage(t, readVideoUpgradeFixture(t, manifestPath)), "p5-poll.beeftv-plugin"); err != nil {
		t.Fatal(err)
	}
	task, err := svc.CreateTask("local", CreateTaskRequest{Type: "canvas_image", Prompt: "P5 receipt fixture",
		Input: map[string]any{"mode": "image", "prompt": "P5 receipt fixture",
			"config": map[string]any{"interfaceType": "codex-image", "model": "codex-image", "baseUrl": upstream.URL, "apiKey": "fixture-key"}}})
	if err != nil {
		t.Fatal(err)
	}
	if err := svc.ProcessNextTask(); err == nil {
		t.Fatal("accepted a response for another provider job")
	}
	stored, err := svc.repo.Task(task.ID)
	if err != nil || stored.ProviderRequestID != "original-job" || polls.Load() != 1 {
		t.Fatalf("original receipt was not preserved: %+v polls=%d error=%v", stored, polls.Load(), err)
	}
}

func TestP5FailedNativeImageRecoversFromOriginalReceipt(t *testing.T) {
	manifestPath := os.Getenv("BEEFTV_P5_CODEX_MANIFEST")
	if manifestPath == "" {
		t.Skip("P5 harness supplies the Codex v3 plugin manifest")
	}
	allowLoopbackProviderTest(t)
	const png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9l8AAAAASUVORK5CYII="
	_, raw, err := generation.DecodeProviderDataURL(png)
	if err != nil {
		t.Fatal(err)
	}
	var creates, polls atomic.Int32
	var restore atomic.Bool
	var owner atomic.Value
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/v1/images/jobs":
			creates.Add(1)
			owner.Store(r.Header.Get("X-BeefTV-Task-ID"))
			_, _ = w.Write([]byte(`{"id":"original-native-image","status":"queued"}`))
		case r.Method == http.MethodGet && r.URL.Path == "/v1/images/jobs/original-native-image":
			polls.Add(1)
			image := "data:image/png;base64,eA=="
			if restore.Load() {
				image = png
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"id": "original-native-image", "status": "succeeded",
				"images": []map[string]string{{"dataUrl": image, "mimeType": "image/png"}},
				"artifacts": []protocol.MediaArtifact{{SchemaVersion: 1, TaskID: owner.Load().(string), ArtifactID: "original-artifact",
					OutputIndex: 0, SHA256: fmt.Sprintf("%x", sha256.Sum256(raw)), SizeBytes: int64(len(raw)), MIMEType: "image/png"}}})
		default:
			t.Errorf("unexpected provider request: %s %s", r.Method, r.URL.Path)
			http.NotFound(w, r)
		}
	}))
	defer upstream.Close()
	dir := t.TempDir()
	svc, db := openDBRuntimeMatrix(t, dir)
	defer closeDBRuntimeMatrix(t, svc, db)
	svc.closeSupportTrace()
	if _, err := svc.InstallPlugin(testPluginPackage(t, readVideoUpgradeFixture(t, manifestPath)), "p5-original-image.beeftv-plugin"); err != nil {
		t.Fatal(err)
	}
	task, err := svc.CreateTask("local", CreateTaskRequest{Type: "canvas_image", Prompt: "P5 recovery fixture",
		Input: map[string]any{"mode": "image", "prompt": "P5 recovery fixture",
			"config": map[string]any{"interfaceType": "codex-image", "model": "codex-image", "baseUrl": upstream.URL, "apiKey": "fixture-key"}}})
	if err != nil {
		t.Fatal(err)
	}
	if err := svc.ProcessNextTask(); err == nil {
		t.Fatal("invalid result bytes were accepted")
	}
	failed, err := svc.repo.Task(task.ID)
	if err != nil || failed.Status != model.TaskStatusFailed || failed.ProviderRequestID != "original-native-image" {
		t.Fatalf("accepted receipt was lost before recovery: %+v %v", failed, err)
	}
	restore.Store(true)
	checkHeld := func(expectedPolls int32) {
		t.Helper()
		current, err := svc.taskDomain().RecoverTask(context.Background(), "local", task.ID)
		if err != nil || current.Status != model.TaskStatusFailed || current.ProviderRequestID != failed.ProviderRequestID ||
			creates.Load() != 1 || polls.Load() != expectedPolls {
			t.Fatalf("failed ingest did not preserve the original task: %+v creates=%d polls=%d error=%v",
				current, creates.Load(), polls.Load(), err)
		}
		var acks int64
		if err := db.Model(&model.MediaDeliveryAck{}).Where("task_id = ?", task.ID).Count(&acks).Error; err != nil || acks != 0 {
			t.Fatalf("failed ingest queued a media ACK: count=%d, error=%v", acks, err)
		}
	}
	blocked := filepath.Join(dir, "resources", "users", "local", "image")
	if err := os.MkdirAll(filepath.Dir(blocked), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(blocked, []byte("fixture blocks resource directory"), 0o600); err != nil {
		t.Fatal(err)
	}
	checkHeld(2)
	if err := os.Remove(blocked); err != nil {
		t.Fatal(err)
	}
	if err := db.Exec(`CREATE TRIGGER fixture_reject_completion BEFORE UPDATE ON tasks
		WHEN NEW.status = 'succeeded' BEGIN SELECT RAISE(ABORT, 'fixture SQLite completion conflict'); END`).Error; err != nil {
		t.Fatal(err)
	}
	checkHeld(3)
	if err := db.Exec("DROP TRIGGER fixture_reject_completion").Error; err != nil {
		t.Fatal(err)
	}
	recovered, err := svc.taskDomain().RecoverTask(context.Background(), "local", task.ID)
	if err != nil || recovered.Status != model.TaskStatusSucceeded || recovered.ID != task.ID || creates.Load() != 1 || polls.Load() != 4 {
		t.Fatalf("original image did not recover: %+v creates=%d polls=%d error=%v", recovered, creates.Load(), polls.Load(), err)
	}
	var delivery struct {
		Artifacts []protocol.MediaArtifact `json:"deliveryArtifacts"`
	}
	if err := json.Unmarshal([]byte(recovered.ResultJSON), &delivery); err != nil || len(delivery.Artifacts) != 1 ||
		delivery.Artifacts[0].ArtifactID != "original-artifact" {
		t.Fatalf("recovery lost the original artifact: %+v %v", delivery, err)
	}
	var ready int64
	if err := db.Model(&model.Resource{}).Where("user_id = ? AND status = ?", "local", model.ResourceStatusReady).Count(&ready).Error; err != nil || ready != 1 {
		t.Fatalf("original result should have exactly one ready resource: count=%d error=%v", ready, err)
	}
}

func TestP5FailedNativeAudioRecoversFromOriginalReceipt(t *testing.T) {
	manifestPath := os.Getenv("BEEFTV_P5_DREAMA_MANIFEST")
	if manifestPath == "" {
		t.Skip("P5 harness supplies the Dreama v3 plugin manifest")
	}
	allowLoopbackProviderTest(t)
	const user, taskID, receipt = "local", "p5-failed-dreama-audio", "original-audio-job"
	raw := readVideoUpgradeFixture(t, "testdata/managed-audio-fixture.mp3")
	var upstream *httptest.Server
	var polls, reads atomic.Int32
	upstream = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("native recovery made a non-read request: %s %s", r.Method, r.URL.Path)
			http.NotFound(w, r)
			return
		}
		switch r.URL.Path {
		case "/audio/v1/audio/tasks/" + receipt:
			polls.Add(1)
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{"id": receipt, "status": "succeeded",
				"audio_url": upstream.URL + "/output.mp3",
				"artifacts": []protocol.MediaArtifact{{SchemaVersion: 1, TaskID: taskID, ArtifactID: "original-audio-artifact",
					OutputIndex: 0, SHA256: fmt.Sprintf("%x", sha256.Sum256(raw)), SizeBytes: int64(len(raw)), MIMEType: "audio/mpeg"}}})
		case "/output.mp3":
			if reads.Add(1) == 1 {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusBadGateway)
				_, _ = w.Write([]byte(`{"id":"foreign-download-receipt"}`))
				return
			}
			w.Header().Set("Content-Type", "audio/mpeg")
			_, _ = w.Write(raw)
		default:
			t.Errorf("native recovery queried a different receipt: %s", r.URL.Path)
			http.NotFound(w, r)
		}
	}))
	defer upstream.Close()
	svc, db := openDBRuntimeMatrix(t, t.TempDir())
	defer closeDBRuntimeMatrix(t, svc, db)
	svc.closeSupportTrace()
	if _, err := svc.InstallPlugin(testPluginPackage(t, readVideoUpgradeFixture(t, manifestPath)), "p5-audio-recovery.beeftv-plugin"); err != nil {
		t.Fatal(err)
	}
	failed := model.Task{ID: taskID, UserID: user, Type: "canvas_audio", Status: model.TaskStatusFailed, ProviderRequestID: receipt,
		InputJSON: mustJSON(canvasGenerationInput{Mode: "audio", Prompt: "Original audio",
			Config: providerConfig{InterfaceType: "dreama-audio-async", Model: "fixture-model", BaseURL: upstream.URL + "/audio",
				APIKey: "fixture-key", AudioFormat: "mp3"}})}
	if err := db.Create(&failed).Error; err != nil {
		t.Fatal(err)
	}
	incomplete, err := svc.taskDomain().RecoverTask(context.Background(), user, taskID)
	if err != nil || incomplete.Status != model.TaskStatusFailed || incomplete.ProviderRequestID != receipt ||
		polls.Load() != 1 || reads.Load() != 1 {
		t.Fatalf("download error replaced original receipt: %+v polls=%d reads=%d error=%v", incomplete, polls.Load(), reads.Load(), err)
	}
	recovered, err := svc.taskDomain().RecoverTask(context.Background(), user, taskID)
	if err != nil || recovered.Status != model.TaskStatusSucceeded || recovered.ProviderRequestID != receipt ||
		polls.Load() != 2 || reads.Load() != 2 {
		t.Fatalf("original audio did not recover: %+v polls=%d reads=%d error=%v", recovered, polls.Load(), reads.Load(), err)
	}
}

func TestP5MediaAckWaitsForBindingAndReplaysAfterRestart(t *testing.T) {
	manifestPath := os.Getenv("BEEFTV_P5_CODEX_MANIFEST")
	if manifestPath == "" {
		t.Skip("P5 harness supplies the Codex v3 plugin manifest")
	}
	allowLoopbackProviderTest(t)
	const (
		user     = "local"
		canvasID = "ack-canvas"
		nodeID   = "ack-node"
		receipt  = "codex-bridge-job"
		png      = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9l8AAAAASUVORK5CYII="
	)
	_, data, err := generation.DecodeProviderDataURL(png)
	if err != nil {
		t.Fatal(err)
	}
	sha := fmt.Sprintf("%x", sha256.Sum256(data))
	var creates, acks atomic.Int32
	var taskID atomic.Value
	var firstRequest atomic.Value
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer fixture-key" {
			t.Error("missing plugin authorization")
		}
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/v1/images/jobs":
			creates.Add(1)
			identity, _ := taskID.Load().(string)
			if r.Header.Get("X-BeefTV-Task-ID") != identity || identity == "" || r.Header.Get("Idempotency-Key") == "" {
				t.Errorf("create lost original task or idempotency identity")
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"id": receipt, "status": "succeeded",
				"images": []map[string]string{{"dataUrl": png, "mimeType": "image/png"}},
				"artifacts": []protocol.MediaArtifact{{SchemaVersion: 1, TaskID: identity, ArtifactID: "codex-output", OutputIndex: 0,
					SHA256: sha, SizeBytes: int64(len(data)), MIMEType: "image/png"}}})
		case r.Method == http.MethodPost && r.URL.Path == "/v1/images/jobs/"+receipt+"/ack":
			body, readErr := io.ReadAll(r.Body)
			var ack protocol.MediaAck
			identity, _ := taskID.Load().(string)
			if readErr != nil || json.Unmarshal(body, &ack) != nil || ack.TaskID != identity || ack.ArtifactID != "codex-output" ||
				ack.ResourceID == "" || ack.SHA256 != sha || ack.SizeBytes != int64(len(data)) || ack.OutputIndex != 0 || ack.AcknowledgedAt.IsZero() {
				t.Errorf("invalid ack body: %s", body)
			}
			key := r.Header.Get("Idempotency-Key")
			if key == "" {
				t.Error("missing stable ack key")
			}
			fingerprint := key + "\n" + string(body)
			if acks.Add(1) == 1 {
				firstRequest.Store(fingerprint)
				w.WriteHeader(http.StatusBadGateway)
				return
			}
			if firstRequest.Load() != fingerprint {
				t.Error("retry used a different ACK body or key")
			}
			_, _ = w.Write([]byte(`{"acknowledged":true,"artifactId":"codex-output","cleanupState":"held"}`))
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
	svc.closeSupportTrace()
	manifest := readVideoUpgradeFixture(t, manifestPath)
	if _, err := svc.InstallPlugin(testPluginPackage(t, manifest), "codex-images.beeftv-plugin"); err != nil {
		t.Fatal(err)
	}
	doc := map[string]any{"id": canvasID, "title": "P5", "revision": 0,
		"nodes": []any{map[string]any{"id": nodeID, "type": "image", "metadata": map[string]any{"taskId": "other-task"}}}, "connections": []any{}}
	if _, err := svc.UpsertUserCanvasProject(user, []byte(mustJSON(doc))); err != nil {
		t.Fatal(err)
	}
	job, err := svc.CreateTask(user, CreateTaskRequest{ProjectID: canvasID, Type: "canvas_image", Prompt: "P5 fixture", Input: map[string]any{
		"mode": "image", "prompt": "P5 fixture", "metadata": map[string]any{"source": "canvas", "nodeId": nodeID},
		"config": map[string]any{"interfaceType": "codex-image", "model": "codex-image", "baseUrl": upstream.URL, "apiKey": "fixture-key"},
	}})
	if err != nil {
		t.Fatal(err)
	}
	taskID.Store(job.ID)
	if err := svc.ProcessNextTask(); err != nil {
		t.Fatal(err)
	}
	stored, err := svc.repo.Task(job.ID)
	if err != nil || stored.Status != model.TaskStatusSucceeded {
		t.Fatalf("native delivery did not finish: %+v %v", stored, err)
	}
	if err := svc.mediaAcks().Process(context.Background()); err != nil {
		t.Fatal(err)
	}
	if acks.Load() != 0 {
		t.Fatal("ack sent before original canvas binding")
	}
	setCanvasBindTestNodeTaskID(t, svc, canvasID, nodeID, job.ID)
	if err := svc.recoverCanvasTaskBindings(*stored); err != nil {
		t.Fatal(err)
	}
	if err := svc.mediaAcks().Process(context.Background()); err != nil {
		t.Fatal(err)
	}
	if acks.Load() != 1 {
		var pendingRows []model.MediaDeliveryAck
		_ = db.Find(&pendingRows).Error
		t.Fatalf("first ack attempts: %d, rows=%+v", acks.Load(), pendingRows)
	}
	closeDBRuntimeMatrix(t, svc, db)
	svc = nil
	svc, db = openDBRuntimeMatrix(t, dir)
	svc.closeSupportTrace()
	makeDue := func() {
		t.Helper()
		if err := db.Model(&model.MediaDeliveryAck{}).Where("task_id = ?", job.ID).Update("next_attempt_at", time.Now().UTC().Add(-time.Second)).Error; err != nil {
			t.Fatal(err)
		}
	}
	setCanvasBindTestNodeTaskID(t, svc, canvasID, nodeID, "replaced-after-restart")
	makeDue()
	if err := svc.mediaAcks().Process(context.Background()); err != nil {
		t.Fatal(err)
	}
	if acks.Load() != 1 {
		t.Fatal("historical receipt was accepted after the node changed ownership")
	}
	setCanvasBindTestNodeTaskID(t, svc, canvasID, nodeID, job.ID)
	makeDue()
	if err := svc.mediaAcks().Process(context.Background()); err != nil {
		t.Fatal(err)
	}
	var pending model.MediaDeliveryAck
	if err := db.Where("task_id = ?", job.ID).First(&pending).Error; err != nil || pending.ConfirmedAt == nil {
		t.Fatalf("durable acknowledgement: %+v %v", pending, err)
	}
	if creates.Load() != 1 || acks.Load() != 2 {
		t.Fatalf("provider creates=%d acknowledgements=%d", creates.Load(), acks.Load())
	}
}
