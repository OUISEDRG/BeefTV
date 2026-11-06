package app

import (
	"context"
	"encoding/json"
	"errors"

	"infinite-canvas/backend/internal/canvasgeneration"
	"infinite-canvas/backend/internal/operations"
	"infinite-canvas/backend/internal/workspace"
)

// Narrow composition adapter: preparation has no task-creation or bridge port.
func (s *operationSession) PrepareCanvasGeneration(ctx context.Context, userID string, canvas json.RawMessage, nodeIDs []string, expectedRevision *int64) (any, error) {
	if !s.service.IsLocalMode() {
		return nil, operations.Unsupported("generation_prepare_unavailable", "画布编译预览仅适用于本地工作区")
	}
	store, err := workspace.NewProviderConfig(s.service.dataDir)
	if err != nil {
		return nil, err
	}
	effective, _, err := store.LoadEffectiveModelConfig()
	if err != nil {
		return nil, err
	}
	if expectedRevision != nil && effective.Revision != *expectedRevision {
		return nil, operations.Conflict("generation_config_changed", "模型配置已变化，请重新读取后准备", nil)
	}
	config, err := json.Marshal(effective.Config)
	if err != nil {
		return nil, err
	}
	request := canvasgeneration.Request{SchemaVersion: 1, Canvas: canvas, Config: config, NodeIDs: nodeIDs, Assets: []any{}}
	prepared, err := canvasgeneration.Prepare(ctx, request, effective.Revision, canvasgeneration.RunBundled, func(id string) (canvasgeneration.ResourceMetadata, error) {
		resource, err := s.OwnedReadyResource(userID, id)
		if err != nil {
			return canvasgeneration.ResourceMetadata{}, err
		}
		return canvasgeneration.ResourceMetadata{Kind: resource.Kind, MimeType: resource.MimeType, Size: resource.Size}, nil
	})
	if err != nil {
		var failure *canvasgeneration.Failure
		if errors.As(err, &failure) {
			return nil, operations.PreconditionFailed(failure.Reason, failure.Message, nil)
		}
		return nil, err
	}
	prepared.SubmissionAvailable = true
	return prepared, nil
}

// Only a server-produced snapshot reaches the existing atomic task admission.
func (s *operationSession) SubmitPreparedCanvasGeneration(ctx context.Context, userID, operationID string, canvas json.RawMessage, nodeIDs []string, kind string, configRevision int64, hash string) (any, error) {
	value, err := s.PrepareCanvasGeneration(ctx, userID, canvas, nodeIDs, &configRevision)
	if err != nil {
		return nil, err
	}
	prepared := value.(canvasgeneration.Prepared)
	if hash != prepared.SnapshotHash {
		return nil, operations.Conflict("generation_snapshot_changed", "生成内容已变化，请重新准备并检查最终引用", nil)
	}
	targets := []operations.GenerationTarget{}
	inputs := map[string]map[string]any{}
	for _, item := range prepared.Compiled.Items {
		if kind != "" && kind != item.Kind {
			return nil, operations.InvalidArg("generation_kind_mismatch", "节点生成类型不一致")
		}
		target := operations.GenerationTarget{NodeID: item.NodeID, Kind: item.Kind, ModelKey: item.ModelKey, Prompt: item.Prompt, Metadata: item.Metadata}
		connection, err := s.service.productionModelConfig(target)
		if err != nil {
			return nil, err
		}
		choice, err := s.ResolveAssistantGenerationModel(item.Kind, item.ModelKey)
		if err != nil {
			return nil, err
		}
		if choice.Revision != configRevision {
			return nil, operations.Conflict("generation_config_changed", "模型配置已变化，请重新准备", nil)
		}
		input, err := canvasgeneration.TaskInput(item, connection)
		if err != nil {
			return nil, operations.PreconditionFailed("generation_compiler_contract", err.Error(), nil)
		}
		// Hash the exact preparation into the existing replay identity.
		input["metadata"].(map[string]any)["generationSnapshotHash"] = hash
		target.Metadata = map[string]any{"generationSnapshotHash": hash}
		targets = append(targets, target)
		inputs[item.NodeID] = input
	}
	return s.submitExternalInputs(userID, operationID, prepared.CanvasID, targets, inputs)
}
