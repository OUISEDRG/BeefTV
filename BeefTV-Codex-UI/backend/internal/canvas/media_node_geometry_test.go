package canvas

import (
	"encoding/json"
	"math"
	"reflect"
	"testing"
)

func TestCreateCanvasNodesGridKeepsExistingNodesAndTitleSpace(t *testing.T) {
	var doc map[string]any
	if err := json.Unmarshal([]byte(`{"nodes":[{"id":"existing","type":"image","title":"existing","position":{"x":600,"y":160},"width":900,"height":220,"metadata":{"locked":true}}]}`), &doc); err != nil {
		t.Fatal(err)
	}
	original, _ := json.Marshal(doc["nodes"])
	drafts := []NodeDraft{
		{Title: "image", Type: "image"}, {Title: "audio", Type: "audio"},
		{Title: "script", Type: "script"}, {Title: "video", Type: "video"},
		{Title: "table", Type: "batch-table"}, {Title: "frame", Type: "frame"},
		{Title: "text", Type: "text"}, {Title: "image2", Type: "image"},
	}
	created, err := createCanvasNodes(doc, drafts)
	if err != nil || len(created) != len(drafts) {
		t.Fatalf("create: %v %v", created, err)
	}
	nodes := canvasDocNodes(doc)
	retained, _ := json.Marshal(nodes[:1])
	if string(retained) != string(original) {
		t.Fatal("existing node was changed")
	}
	for index, raw := range nodes[1:] {
		node := raw.(map[string]any)
		position := node["position"].(map[string]any)
		if position["x"].(float64) < 1500+nodePlacementGap {
			t.Fatalf("node %d overlaps the existing canvas", index)
		}
		if index >= nodePlacementColumns {
			above := nodes[1+index-nodePlacementColumns].(map[string]any)
			abovePosition := above["position"].(map[string]any)
			if position["x"] != abovePosition["x"] || position["y"].(float64) < abovePosition["y"].(float64)+above["height"].(float64)+nodePlacementRowGap {
				t.Fatalf("node %d lost column alignment or title clearance", index)
			}
		}
	}
	assertMediaGridClearance(t, nodes[1:])
	// Binding a mix of portrait, landscape and panoramic media must not grow
	// beyond the cells used to place the original batch.
	for _, raw := range nodes[1:] {
		node := raw.(map[string]any)
		if node["type"] == "video" {
			FitBoundMediaNodeGeometry(node, 3840, 1080)
		} else {
			FitBoundMediaNodeGeometry(node, 1080, 1920)
		}
	}
	assertMediaGridClearance(t, nodes[1:])
}

func assertMediaGridClearance(t *testing.T, nodes []any) {
	t.Helper()
	for index, raw := range nodes {
		first := raw.(map[string]any)
		p := first["position"].(map[string]any)
		x, y := p["x"].(float64), p["y"].(float64)
		for _, other := range nodes[index+1:] {
			second := other.(map[string]any)
			q := second["position"].(map[string]any)
			qx, qy := q["x"].(float64), q["y"].(float64)
			if x+first["width"].(float64)+nodePlacementGap > qx && qx+second["width"].(float64)+nodePlacementGap > x &&
				y+first["height"].(float64)+nodePlacementRowGap > qy && qy+second["height"].(float64)+nodePlacementRowGap > y {
				t.Fatalf("cards %v and %v overlap their title clearance", first["title"], second["title"])
			}
		}
	}
}

func TestFitBoundMediaNodeGeometryPreservesImportBoundsAndCoordinates(t *testing.T) {
	for _, test := range []struct {
		name, kind                                 string
		width, height, naturalWidth, naturalHeight float64
		wantWidth, wantHeight                      float64
	}{
		{"landscape", "image", 400, 280, 1920, 1080, 400, 225},
		{"portrait", "image", 400, 280, 1080, 1920, 157.5, 280},
		{"panorama", "video", 400, 280, 3840, 1080, 400, 112.5},
		{"saved large ratio", "image", 1600, 900, 1920, 1080, 1600, 900},
		{"small original", "image", 400, 280, 3, 2, 400, 400.0 / 1.5},
		{"invalid frame", "image", 0, 1, 1080, 1920, 227.8125, 405},
		{"audio", "audio", 400, 280, 1920, 1080, 400, 280},
	} {
		t.Run(test.name, func(t *testing.T) {
			position := map[string]any{"x": 24.0, "y": -75.0}
			node := map[string]any{"type": test.kind, "position": position, "width": test.width, "height": test.height}
			FitBoundMediaNodeGeometry(node, int(test.naturalWidth), int(test.naturalHeight))
			if math.Abs(node["width"].(float64)-test.wantWidth) > 0.001 || math.Abs(node["height"].(float64)-test.wantHeight) > 0.001 {
				t.Fatalf("wrong saved bounds: %v", node)
			}
			if !reflect.DeepEqual(node["position"], map[string]any{"x": 24.0, "y": -75.0}) {
				t.Fatal("explicit position changed")
			}
			before, _ := json.Marshal(node)
			FitBoundMediaNodeGeometry(node, int(test.naturalWidth), int(test.naturalHeight))
			after, _ := json.Marshal(node)
			if string(before) != string(after) {
				t.Fatal("repeat binding changed geometry")
			}
		})
	}
}

func TestFitBoundMediaNodeGeometryPreservesUserAndWorkflowGeometry(t *testing.T) {
	for _, metadata := range []map[string]any{
		{"freeResize": true}, {"manualSize": true}, {"locked": true}, {"workflowSlot": map[string]any{"nodeId": "slot"}},
		{"isBatchRoot": true}, {"batchRootId": "root"}, {"status": "loading"},
	} {
		node := map[string]any{"type": "image", "width": 400.0, "height": 280.0, "metadata": metadata}
		FitBoundMediaNodeGeometry(node, 1080, 1920)
		if node["width"] != 400.0 || node["height"] != 280.0 {
			t.Fatalf("explicit layout changed: %v", node)
		}
	}
}

func TestCreateCanvasNodesReservesVisibleTitleGapAtSavedZoom(t *testing.T) {
	for _, test := range []struct {
		name        string
		scale       any
		gap, rowGap float64
		columnWidth float64
	}{
		{"normal", 1.0, 80, 140, 720},
		{"quarter", 0.25, 96, 192, 720},
		{"missing", nil, 96, 192, 720},
		{"invalid", -1.0, 96, 192, 720},
		{"minimum header scale", 0.01, 480, 960, 3200},
	} {
		t.Run(test.name, func(t *testing.T) {
			doc := map[string]any{"nodes": []any{}, "viewport": map[string]any{"k": test.scale}}
			_, err := createCanvasNodes(doc, []NodeDraft{
				{Title: "1", Type: "image"}, {Title: "2", Type: "image"},
				{Title: "3", Type: "image"}, {Title: "4", Type: "image"}, {Title: "5", Type: "image"},
			})
			if err != nil {
				t.Fatal(err)
			}
			nodes := canvasDocNodes(doc)
			first, second, nextRow := nodes[0].(map[string]any), nodes[1].(map[string]any), nodes[4].(map[string]any)
			p, q, r := first["position"].(map[string]any), second["position"].(map[string]any), nextRow["position"].(map[string]any)
			if q["x"].(float64)-p["x"].(float64)-test.columnWidth != test.gap || r["y"].(float64)-p["y"].(float64)-first["height"].(float64) != test.rowGap {
				t.Fatalf("incorrect visible gaps at scale %v: %v %v %v", test.scale, p, q, r)
			}
		})
	}
}

func TestCreateCanvasNodesKeepsSelectedTitlesClearForNarrowCards(t *testing.T) {
	var doc map[string]any
	if err := json.Unmarshal([]byte(`{"nodes":[{"id":"existing","type":"audio","title":"existing","position":{"x":100,"y":160},"width":340,"height":120}],"viewport":{"k":0.25}}`), &doc); err != nil {
		t.Fatal(err)
	}
	if _, err := createCanvasNodes(doc, []NodeDraft{{Title: "one", Type: "audio"}, {Title: "two", Type: "text"}}); err != nil {
		t.Fatal(err)
	}
	nodes := canvasDocNodes(doc)
	first, second := nodes[1].(map[string]any), nodes[2].(map[string]any)
	x := first["position"].(map[string]any)["x"].(float64)
	nextX := second["position"].(map[string]any)["x"].(float64)
	if x != 100+640+96 || nextX-x != 640+96 {
		t.Fatalf("selected 160px titles overlap at 25%%: x=%v next=%v", x, nextX)
	}
}
