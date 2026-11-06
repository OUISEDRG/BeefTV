// Package canvasgeneration invokes the same reference compiler used by the
// canvas. Preparation has no task, upload, provider or payment dependency.
package canvasgeneration

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"strings"
)

type Request struct {
	SchemaVersion int             `json:"schemaVersion"`
	Canvas        json.RawMessage `json:"canvas"`
	Config        json.RawMessage `json:"config"`
	NodeIDs       []string        `json:"nodeIds"`
	Assets        []any           `json:"assets"`
}

type Reference struct {
	ID         string  `json:"id"`
	StorageKey string  `json:"storageKey"`
	MimeType   string  `json:"mimeType,omitempty"`
	Width      int     `json:"width,omitempty"`
	Height     int     `json:"height,omitempty"`
	DurationMs float64 `json:"durationMs,omitempty"`
}

type Item struct {
	NodeID          string          `json:"nodeId"`
	Kind            string          `json:"kind"`
	ModelKey        string          `json:"modelKey"`
	Prompt          string          `json:"prompt"`
	Config          map[string]any  `json:"config"`
	Metadata        map[string]any  `json:"metadata"`
	ReferenceImages []Reference     `json:"referenceImages"`
	ReferenceVideos []Reference     `json:"referenceVideos"`
	ReferenceAudios []Reference     `json:"referenceAudios"`
	Preview         json.RawMessage `json:"preview"`
}

type Compiled struct {
	SchemaVersion int    `json:"schemaVersion"`
	Items         []Item `json:"items"`
}

type PublicItem struct {
	NodeID   string          `json:"nodeId"`
	Kind     string          `json:"kind"`
	ModelKey string          `json:"modelKey"`
	Preview  json.RawMessage `json:"preview"`
}

type Prepared struct {
	Compiled            Compiled     `json:"-"`
	CanvasID            string       `json:"canvasId"`
	CanvasRevision      int64        `json:"canvasRevision"`
	ModelConfigRevision int64        `json:"modelConfigRevision"`
	SnapshotHash        string       `json:"snapshotHash"`
	Items               []PublicItem `json:"items"`
	GenerationSubmitted bool         `json:"generationSubmitted"`
	SubmissionAvailable bool         `json:"submissionAvailable"`
}

type Failure struct {
	Reason  string `json:"reason"`
	Message string `json:"message"`
}

func (e *Failure) Error() string           { return e.Message }
func failure(reason, message string) error { return &Failure{Reason: reason, Message: message} }

type Runner func(context.Context, Request) (Compiled, error)

// ResourceMetadata is returned only after the host checks resource ownership and readiness.
type ResourceMetadata struct {
	Kind     string
	MimeType string
	Size     int64
}

func Prepare(ctx context.Context, request Request, configRevision int64, run Runner, ownedReady func(string) (ResourceMetadata, error)) (Prepared, error) {
	var result Prepared
	var canvas struct {
		ID       string `json:"id"`
		Revision int64  `json:"revision"`
	}
	if json.Unmarshal(request.Canvas, &canvas) != nil || canvas.ID == "" || canvas.Revision < 1 || len(request.NodeIDs) < 1 || len(request.NodeIDs) > 8 {
		return result, failure("invalid_generation_prepare", "请选择当前画布的 1 至 8 个生成节点")
	}
	compiled, err := run(ctx, request)
	if err != nil {
		return result, err
	}
	if compiled.SchemaVersion != 1 || len(compiled.Items) != len(request.NodeIDs) {
		return result, failure("generation_compiler_contract", "画布编译器返回内容不完整")
	}
	result = Prepared{CanvasID: canvas.ID, CanvasRevision: canvas.Revision, ModelConfigRevision: configRevision, Items: []PublicItem{}}
	seen := map[string]bool{}
	for i, item := range compiled.Items {
		if item.NodeID != request.NodeIDs[i] || seen[item.NodeID] || (item.Kind != "video" && item.Kind != "image") || item.ModelKey == "" || len(item.Preview) == 0 || string(item.Preview) == "null" {
			return Prepared{}, failure("generation_compiler_contract", "画布编译器返回的节点身份或类型无效")
		}
		seen[item.NodeID] = true
		for _, refs := range [][]Reference{item.ReferenceImages, item.ReferenceVideos, item.ReferenceAudios} {
			for _, ref := range refs {
				if ref.ID == "" || !strings.HasPrefix(ref.StorageKey, "resource:") || strings.TrimPrefix(ref.StorageKey, "resource:") == "" {
					return Prepared{}, failure("generation_reference_not_saved", "参考素材必须先保存到当前工作区")
				}
				if _, err := ownedReady(strings.TrimPrefix(ref.StorageKey, "resource:")); err != nil {
					return Prepared{}, failure("generation_reference_unavailable", "参考素材未就绪或不属于当前工作区")
				}
			}
		}
		if err := validateVoiceResources(item, ownedReady); err != nil {
			return Prepared{}, err
		}
		result.Items = append(result.Items, PublicItem{NodeID: item.NodeID, Kind: item.Kind, ModelKey: item.ModelKey, Preview: item.Preview})
	}
	// Credential-free compiled inputs and both revisions identify this exact
	// preparation. The hash is evidence, never authority to submit a task.
	encoded, err := json.Marshal(struct {
		CanvasID       string
		CanvasRevision int64
		ConfigRevision int64
		Compiled       Compiled
	}{canvas.ID, canvas.Revision, configRevision, compiled})
	if err != nil {
		return Prepared{}, failure("generation_compiler_contract", "画布编译结果无法保存")
	}
	result.SnapshotHash = fmt.Sprintf("%x", sha256.Sum256(encoded))
	result.Compiled = compiled
	return result, nil
}
