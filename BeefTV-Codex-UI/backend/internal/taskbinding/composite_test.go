package taskbinding

import (
	"strings"
	"testing"

	"infinite-canvas/backend/internal/model"
	localtask "infinite-canvas/backend/internal/task"
)

type compositePorts struct {
	fakePorts
	resources map[string]*model.Resource
}

func (p *compositePorts) OwnedReadyResource(_ string, id string) (*model.Resource, error) {
	return p.resources[id], nil
}

func compositeFixture(edit string) (*compositePorts, Request) {
	task := testImageTask()
	task.InputJSON = `{"metadata":{"nodeId":"node-1","edit":"` + edit + `","emotion":{"sourceWidth":800,"sourceHeight":600}},"referenceImages":[{"storageKey":"resource:source"}]}`
	return &compositePorts{
		fakePorts: fakePorts{
			task:    task,
			outputs: []localtask.CanonicalOutput{{OutputIndex: 0, MediaType: "image", MaterializedAssetID: "raw-asset", ResourceID: "raw-resource"}},
			asset:   &model.Asset{ID: "composite-asset", UserID: "user-1", Kind: "image", PayloadJSON: `{"kind":"image","data":{"storageKey":"resource:composite"},"metadata":{"source":"canvas-generation","canvasId":"canvas-1","nodeId":"node-1","taskId":"task-1"}}`},
		},
		resources: map[string]*model.Resource{
			"source":    {ID: "source", UserID: "user-1", Kind: "image", Status: model.ResourceStatusReady, Width: 800, Height: 600},
			"composite": {ID: "composite", UserID: "user-1", Kind: "image", Status: model.ResourceStatusReady, Width: 800, Height: 600},
		},
	}, Request{CanvasID: "canvas-1", TaskID: "task-1", NodeID: "node-1", CompositeAssetID: "composite-asset"}
}

func TestBindCompositeUsesPersistedFullImage(t *testing.T) {
	for _, edit := range []string{"emotion", "mask-guide"} {
		t.Run(edit, func(t *testing.T) {
			ports, req := compositeFixture(edit)
			receipt, err := Bind(ports, "user-1", req)
			if err != nil {
				t.Fatal(err)
			}
			if ports.binds != 1 || receipt.AssetID != "composite-asset" || receipt.ResourceID != "composite" || receipt.StorageKey != "resource:composite" || receipt.Content != "/api/resources/composite/file" {
				t.Fatalf("wrong composite receipt: %#v, binds=%d", receipt, ports.binds)
			}
			if ports.lastPatch.Width != 800 || ports.lastPatch.Height != 600 || ports.lastPatch.TaskOutcome == nil || ports.lastPatch.TaskOutcome.Phase != "ready" {
				t.Fatalf("wrong full-image dimensions/outcome: %#v", ports.lastPatch)
			}
			if ports.outputs[0].ResourceID != "raw-resource" || ports.outputs[0].MaterializedAssetID != "raw-asset" {
				t.Fatal("binding changed the canonical model output")
			}
		})
	}
}

func TestBindCompositeRefusesWrongIdentityOrIncompleteOutput(t *testing.T) {
	tests := []struct {
		name, reason string
		change       func(*compositePorts, *Request)
	}{
		{"failed task", "task_not_succeeded", func(p *compositePorts, _ *Request) { p.task.Status = model.TaskStatusFailed }},
		{"running task", "task_not_succeeded", func(p *compositePorts, _ *Request) { p.task.Status = model.TaskStatusRunning }},
		{"foreign task", "task_foreign", func(p *compositePorts, _ *Request) { p.task.UserID = "other" }},
		{"missing canvas", "canvas_mismatch", func(p *compositePorts, _ *Request) { p.task.ProjectID = "" }},
		{"wrong canvas", "canvas_mismatch", func(_ *compositePorts, r *Request) { r.CanvasID = "other" }},
		{"wrong node", "node_mismatch", func(_ *compositePorts, r *Request) { r.NodeID = "other" }},
		{"missing task node", "node_mismatch", func(p *compositePorts, _ *Request) {
			p.task.InputJSON = strings.ReplaceAll(p.task.InputJSON, `"nodeId":"node-1",`, "")
		}},
		{"wrong output", "invalid_params", func(_ *compositePorts, r *Request) { r.OutputIndex = 1 }},
		{"ordinary image", "invalid_params", func(p *compositePorts, _ *Request) {
			p.task.InputJSON = strings.ReplaceAll(p.task.InputJSON, `"edit":"emotion"`, `"edit":"mask"`)
		}},
		{"no durable output", "output_not_ready", func(p *compositePorts, _ *Request) { p.outputs = nil }},
		{"unmaterialized output", "output_not_ready", func(p *compositePorts, _ *Request) { p.outputs[0].MaterializedAssetID = "" }},
		{"missing asset", "output_not_ready", func(p *compositePorts, _ *Request) { p.asset = nil }},
		{"foreign asset", "asset_foreign", func(p *compositePorts, _ *Request) { p.asset.UserID = "other" }},
		{"wrong asset id", "output_not_ready", func(p *compositePorts, _ *Request) { p.asset.ID = "other" }},
		{"nonimage asset", "composite_asset_mismatch", func(p *compositePorts, _ *Request) { p.asset.Kind = "video" }},
		{"asset wrong canvas", "composite_asset_mismatch", func(p *compositePorts, _ *Request) {
			p.asset.PayloadJSON = strings.ReplaceAll(p.asset.PayloadJSON, "canvas-1", "other")
		}},
		{"asset wrong node", "composite_asset_mismatch", func(p *compositePorts, _ *Request) {
			p.asset.PayloadJSON = strings.ReplaceAll(p.asset.PayloadJSON, "node-1", "other")
		}},
		{"asset wrong task", "composite_asset_mismatch", func(p *compositePorts, _ *Request) {
			p.asset.PayloadJSON = strings.ReplaceAll(p.asset.PayloadJSON, "task-1", "other")
		}},
		{"asset wrong source", "composite_asset_mismatch", func(p *compositePorts, _ *Request) {
			p.asset.PayloadJSON = strings.ReplaceAll(p.asset.PayloadJSON, "canvas-generation", "upload")
		}},
		{"raw output disguised as composite", "composite_asset_mismatch", func(p *compositePorts, _ *Request) {
			p.asset.PayloadJSON = strings.ReplaceAll(p.asset.PayloadJSON, "resource:composite", "resource:raw-resource")
		}},
		{"external resource", "composite_asset_mismatch", func(p *compositePorts, _ *Request) {
			p.asset.PayloadJSON = strings.ReplaceAll(p.asset.PayloadJSON, "resource:composite", "https://example.com/image.png")
		}},
		{"pending resource", "resource_not_ready", func(p *compositePorts, _ *Request) { p.resources["composite"].Status = model.ResourceStatusPending }},
		{"foreign resource", "resource_foreign", func(p *compositePorts, _ *Request) { p.resources["composite"].UserID = "other" }},
		{"nonimage resource", "resource_mismatch", func(p *compositePorts, _ *Request) { p.resources["composite"].Kind = "video" }},
		{"cropped output", "composite_size_mismatch", func(p *compositePorts, _ *Request) { p.resources["composite"].Width = 300 }},
		{"missing source dimensions", "composite_source_missing", func(p *compositePorts, _ *Request) {
			p.task.InputJSON = strings.ReplaceAll(p.task.InputJSON, `"sourceWidth":800`, `"sourceWidth":0`)
		}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ports, req := compositeFixture("emotion")
			tt.change(ports, &req)
			if _, err := Bind(ports, "user-1", req); !isBindReason(err, tt.reason) {
				t.Fatalf("got %v, want %s", err, tt.reason)
			}
			if ports.binds != 0 {
				t.Fatal("invalid composite wrote the canvas")
			}
		})
	}
}

func TestMaskCompositeRequiresFrozenOwnedImageDimensions(t *testing.T) {
	for _, state := range []string{"missing", "foreign", "pending", "wrong id", "video", "zero size", "wrong size", "missing reference"} {
		t.Run(state, func(t *testing.T) {
			ports, req := compositeFixture("mask-guide")
			reason := "composite_source_missing"
			switch state {
			case "missing":
				delete(ports.resources, "source")
			case "foreign":
				ports.resources["source"].UserID = "other"
			case "pending":
				ports.resources["source"].Status = model.ResourceStatusPending
			case "wrong id":
				ports.resources["source"].ID = "other"
			case "video":
				ports.resources["source"].Kind = "video"
			case "zero size":
				ports.resources["source"].Height = 0
			case "wrong size":
				ports.resources["source"].Width = 900
				reason = "composite_size_mismatch"
			case "missing reference":
				ports.task.InputJSON = strings.ReplaceAll(ports.task.InputJSON, "resource:source", "")
			}
			if _, err := Bind(ports, "user-1", req); !isBindReason(err, reason) || ports.binds != 0 {
				t.Fatalf("got %v, want %s without write, binds=%d", err, reason, ports.binds)
			}
		})
	}
}
