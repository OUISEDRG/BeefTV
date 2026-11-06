package operations

import (
	"context"
	"encoding/json"
	"strings"
)

type CanvasGenerationPreparation interface {
	PrepareCanvasGeneration(context.Context, string, json.RawMessage, []string, *int64) (any, error)
}

func registerGenerationPrepare(r *Registry) {
	r.Register(Op{ID: "canvas.generation.prepare", Summary: "只读编译剧梦视频节点，检查剧集绑定、参数、素材引用与音色，返回最终预览和快照摘要；不上传、不创建远端分镜、不排队、不生成或扣费。外部调用方获授权后可将 snapshotHash 和两个版本交给 canvas.generation.submit。", ReadOnly: true, AssistantCanvas: true, Scope: ScopeCanvas,
		Params: json.RawMessage(`{"type":"object","properties":{"canvasId":{"type":"string"},"nodeIds":{"type":"array","items":{"type":"string"},"minItems":1,"maxItems":8},"expectedRevision":{"type":"integer","minimum":1},"expectedModelConfigRevision":{"type":"integer","minimum":0}},"required":["canvasId","nodeIds"],"additionalProperties":false}`), Handler: opGenerationPrepare})
}

func opGenerationPrepare(ctx *Context, raw json.RawMessage) (any, error) {
	var args struct {
		CanvasID                    string   `json:"canvasId"`
		NodeIDs                     []string `json:"nodeIds"`
		ExpectedRevision            *int64   `json:"expectedRevision"`
		ExpectedModelConfigRevision *int64   `json:"expectedModelConfigRevision"`
	}
	if err := decodeParams(raw, &args); err != nil {
		return nil, err
	}
	if strings.TrimSpace(args.CanvasID) == "" || len(args.NodeIDs) < 1 || len(args.NodeIDs) > 8 {
		return nil, InvalidArg("invalid_batch", "canvasId 和 1 至 8 个 nodeIds 必填")
	}
	seen := map[string]bool{}
	for _, id := range args.NodeIDs {
		if strings.TrimSpace(id) == "" || seen[id] {
			return nil, InvalidArg("invalid_batch", "生成节点 ID 不能为空或重复")
		}
		seen[id] = true
	}
	if args.ExpectedRevision != nil && *args.ExpectedRevision < 1 || args.ExpectedModelConfigRevision != nil && *args.ExpectedModelConfigRevision < 0 {
		return nil, InvalidArg("invalid_revision", "版本号无效")
	}
	docRaw, err := ctx.Domain.UserCanvasProject(ctx.UserID, args.CanvasID)
	if err != nil {
		return nil, mapDomainError(err)
	}
	var doc map[string]any
	if json.Unmarshal(docRaw, &doc) != nil {
		return nil, PreconditionFailed("canvas_unreadable", "当前画布无法读取", nil)
	}
	if args.ExpectedRevision != nil && canvasRevision(doc) != *args.ExpectedRevision {
		return nil, Conflict("revision_conflict", "画布已变化，请重新读取后准备", nil)
	}
	port, ok := ctx.Domain.(CanvasGenerationPreparation)
	if !ok {
		return nil, Unsupported("generation_prepare_unavailable", "当前工作区不支持画布生成预览")
	}
	requestContext := ctx.Context
	if requestContext == nil {
		requestContext = context.Background()
	}
	return port.PrepareCanvasGeneration(requestContext, ctx.UserID, docRaw, args.NodeIDs, args.ExpectedModelConfigRevision)
}
