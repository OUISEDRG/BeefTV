package generation

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"testing"
	"time"

	"infinite-canvas/backend/internal/protocol"
)

func TestP5MediaArtifactRequiresOriginalTaskAndBytes(t *testing.T) {
	const png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9l8AAAAASUVORK5CYII="
	_, data, err := DecodeProviderDataURL(png)
	if err != nil {
		t.Fatal(err)
	}
	ctx := WithCallMeta(context.Background(), CallMeta{TaskID: "owned-task"})
	result := &protocol.Result{Images: []protocol.MediaReference{{DataURL: png, MIMEType: "image/png"}}, Artifacts: []protocol.MediaArtifact{{
		SchemaVersion: 1, TaskID: "owned-task", ArtifactID: "artifact-1", OutputIndex: 0,
		MIMEType: "image/png", SizeBytes: int64(len(data)), SHA256: fmt.Sprintf("%x", sha256.Sum256(data)),
	}}}
	output, err := FinishProtocolResult(ctx, Config{}, "image", "bridge-job", result, VideoPollPolicy{})
	if err != nil || output["deliveryArtifacts"] == nil {
		t.Fatalf("verified artifact was not preserved: %#v, %v", output, err)
	}
	for _, test := range []struct {
		name   string
		modify func(*protocol.MediaArtifact)
	}{
		{"foreign task", func(a *protocol.MediaArtifact) { a.TaskID = "different" }},
		{"wrong index", func(a *protocol.MediaArtifact) { a.OutputIndex = 1 }},
		{"wrong size", func(a *protocol.MediaArtifact) { a.SizeBytes++ }},
		{"wrong hash", func(a *protocol.MediaArtifact) { a.SHA256 = fmt.Sprintf("%064x", 0) }},
	} {
		t.Run(test.name, func(t *testing.T) {
			altered := *result
			artifact := result.Artifacts[0]
			test.modify(&artifact)
			altered.Artifacts = []protocol.MediaArtifact{artifact}
			if _, err := FinishProtocolResult(ctx, Config{}, "image", "bridge-job", &altered, VideoPollPolicy{}); err == nil {
				t.Fatal("accepted media without matching verification")
			}
		})
	}
}

func TestP5ManifestDeclaresScopedMediaAck(t *testing.T) {
	const manifest = `{"apiVersion":"beeftv.plugin/v3","id":"delivery-fixture","name":"Fixture","version":"1.0.0","enabled":true,
	"permissions":["generation.run","media.read"],"contributes":{"providers":[{"id":"delivery-fixture","label":"Fixture","capabilities":["image"],"scopes":["canvas"],
	"create":{"method":"POST","path":"/v1/jobs"},"poll":{"method":"GET","path":"/v1/jobs/{{taskId}}"},
	"ack":{"method":"POST","path":"/v1/jobs/{{taskId}}/ack","originPath":true,
	"headers":{"Idempotency-Key":{"$ref":"request.extra.mediaAckKey"}},"body":{"$ref":"request.extra.mediaAck"}},
	"response":{"taskId":{"$ref":"response.id"},"status":{"$ref":"response.status"},"artifacts":{"$ref":"response.artifacts"}}}]}}`
	adapter, err := protocol.LoadManifest([]byte(manifest))
	if err != nil {
		t.Fatal(err)
	}
	acknowledger, ok := adapter.(protocol.MediaAckAdapter)
	if !ok || !acknowledger.MediaAckAvailable() {
		t.Fatal("manifest acknowledgement capability unavailable")
	}
	ack := protocol.MediaAck{SchemaVersion: 1, TaskID: "beef-task", ArtifactID: "artifact-1", ResourceID: "resource-1",
		SHA256: fmt.Sprintf("%064x", 1), SizeBytes: 3, AcknowledgedAt: time.Date(2026, 11, 7, 4, 15, 0, 0, time.UTC)}
	spec, err := acknowledger.BuildMediaAck(context.Background(), protocol.PollContext{TaskID: "bridge-job"}, ack, "stable-key")
	if err != nil || spec.Method != "POST" || spec.Path != "/v1/jobs/bridge-job/ack" || !spec.OriginPath || spec.Headers["Idempotency-Key"] != "stable-key" {
		t.Fatalf("ack specification: %+v %v", spec, err)
	}
	body, _ := json.Marshal(spec.Body)
	want, _ := json.Marshal(ack)
	var gotAck protocol.MediaAck
	if err := json.Unmarshal(body, &gotAck); err != nil || gotAck != ack {
		t.Fatalf("ack body %s, want %s: %v", body, want, err)
	}
	foreign := []byte(`{"id":"another-bridge-job","status":"succeeded","artifacts":[{}]}`)
	if _, err := adapter.ParsePoll(context.Background(), protocol.PollContext{TaskID: "bridge-job"}, foreign); err == nil {
		t.Fatal("artifact was accepted from another provider job")
	}
}

func TestP5BridgeManifestsDeclareMediaAck(t *testing.T) {
	for _, test := range []struct {
		env  string
		want int
	}{{"BEEFTV_P5_DREAMA_MANIFEST", 5}, {"BEEFTV_P5_CODEX_MANIFEST", 1}} {
		t.Run(test.env, func(t *testing.T) {
			path := os.Getenv(test.env)
			if path == "" {
				t.Skip("P5 harness supplies the Bridge manifest")
			}
			data, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			adapters, err := protocol.LoadInstalledProviders(data, nil)
			if err != nil {
				t.Fatal(err)
			}
			found := 0
			for _, adapter := range adapters {
				if ack, ok := adapter.(protocol.MediaAckAdapter); ok && ack.MediaAckAvailable() {
					found++
				}
			}
			if found != test.want {
				t.Fatalf("media acknowledgement providers=%d, want %d", found, test.want)
			}
		})
	}
}
