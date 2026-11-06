package canvas

import "math"

// FitBoundMediaNodeGeometry uses trusted resource dimensions inside the saved
// card bounds. Binding must not enlarge an import grid or move explicit positions.
func FitBoundMediaNodeGeometry(node map[string]any, naturalWidth, naturalHeight int) {
	kind := canvasNodeType(node)
	if (kind != "image" && kind != "video") || naturalWidth <= 0 || naturalHeight <= 0 {
		return
	}
	metadata, _ := node["metadata"].(map[string]any)
	if metadata["workflowSlot"] != nil || metadata["status"] == "loading" || metadata["isBatchRoot"] == true {
		return
	}
	if batchRoot, _ := metadata["batchRootId"].(string); batchRoot != "" {
		return
	}
	for _, key := range []string{"freeResize", "manualSize", "locked"} {
		if value, ok := metadata[key].(bool); ok && value {
			return
		}
	}
	width, _ := node["width"].(float64)
	height, _ := node["height"].(float64)
	if width < 24 || height < 24 || math.IsNaN(width) || math.IsNaN(height) || math.IsInf(width, 0) || math.IsInf(height, 0) {
		descriptor, _ := canvasCapabilityRegistry.Resolve(kind)
		width, height = descriptor.DefaultWidth, descriptor.DefaultHeight
	}
	ratio := float64(naturalWidth) / float64(naturalHeight)
	if node["width"] == width && node["height"] == height && math.Abs(width/height-ratio) <= ratio*0.01 {
		return
	}
	scale := math.Min(width/float64(naturalWidth), height/float64(naturalHeight))
	node["width"] = float64(naturalWidth) * scale
	node["height"] = float64(naturalHeight) * scale
}
