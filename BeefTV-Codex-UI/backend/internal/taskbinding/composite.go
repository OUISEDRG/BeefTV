package taskbinding

import (
	"encoding/json"
	"strings"

	"infinite-canvas/backend/internal/assets"
	"infinite-canvas/backend/internal/model"
)

// A compositor publishes an owned asset before binding. Accept only that
// persisted task/canvas/node result, never an arbitrary client media URL.
func validateCompositeAsset(asset *model.Asset, userID string, req Request, rawResourceID string) error {
	if asset == nil || strings.TrimSpace(asset.ID) != req.CompositeAssetID {
		return precondition("output_not_ready", "合成素材尚未保存")
	}
	if asset.UserID != userID {
		return forbidden("asset_foreign", "合成素材不属于当前用户")
	}
	var payload struct {
		Kind     string `json:"kind"`
		Metadata struct {
			Source   string `json:"source"`
			CanvasID string `json:"canvasId"`
			NodeID   string `json:"nodeId"`
			TaskID   string `json:"taskId"`
		} `json:"metadata"`
	}
	if json.Unmarshal([]byte(asset.PayloadJSON), &payload) != nil || asset.Kind != "image" || payload.Kind != "image" ||
		payload.Metadata.Source != "canvas-generation" || payload.Metadata.CanvasID != req.CanvasID ||
		payload.Metadata.NodeID != req.NodeID || payload.Metadata.TaskID != req.TaskID {
		return precondition("composite_asset_mismatch", "合成素材不属于本次画布节点任务")
	}
	if id := assetResourceID(asset); id == "" || id == strings.TrimSpace(rawResourceID) {
		return precondition("composite_asset_mismatch", "不能把局部编辑的中间产物当作合成结果")
	}
	return nil
}

func validateCompositeDimensions(ports Ports, userID string, task model.Task, resource *model.Resource) error {
	var input struct {
		Metadata struct {
			Edit    string `json:"edit"`
			Emotion struct {
				Width  int `json:"sourceWidth"`
				Height int `json:"sourceHeight"`
			} `json:"emotion"`
		} `json:"metadata"`
		References []struct {
			StorageKey string `json:"storageKey"`
			URL        string `json:"url"`
			DataURL    string `json:"dataUrl"`
		} `json:"referenceImages"`
	}
	if json.Unmarshal([]byte(task.InputJSON), &input) != nil {
		return precondition("composite_source_missing", "无法核验合成结果的原图尺寸")
	}
	var width, height int
	if input.Metadata.Edit == "emotion" {
		width, height = input.Metadata.Emotion.Width, input.Metadata.Emotion.Height
	}
	if input.Metadata.Edit == "mask-guide" && len(input.References) > 0 {
		ref := input.References[0]
		id := assets.ResourceID(ref.StorageKey)
		if id == "" {
			id = assets.ResourceID(ref.URL)
		}
		if id == "" {
			id = assets.ResourceID(ref.DataURL)
		}
		if id != "" {
			source, err := ports.OwnedReadyResource(userID, id)
			if err != nil {
				return mapPortError(err)
			}
			if source != nil && source.ID == id && source.UserID == userID && source.Status == model.ResourceStatusReady && source.Kind == "image" {
				width, height = source.Width, source.Height
			}
		}
	}
	if width <= 0 || height <= 0 {
		return precondition("composite_source_missing", "无法核验合成结果的原图尺寸")
	}
	if resource.Width != width || resource.Height != height {
		return precondition("composite_size_mismatch", "合成结果尺寸与任务原图不一致")
	}
	return nil
}
