package mediaattempt

import (
	"context"
	"encoding/json"
	"errors"
	"strings"

	"infinite-canvas/backend/internal/generation"
	"infinite-canvas/backend/internal/model"
	"infinite-canvas/backend/internal/protocol"
	localtask "infinite-canvas/backend/internal/task"
)

// Recover a Bridge result through its saved job ID. This path cannot call Create.
func (s *Service) recoverBridgeImage(ctx context.Context, task model.Task) ([]byte, string, bool, error) {
	raw, err := s.secrets.DecryptInputJSON(task.InputJSON)
	if err != nil {
		return nil, "", false, nil
	}
	var input generation.Input
	if json.Unmarshal([]byte(raw), &input) != nil || input.Mode != "image" {
		return nil, "", false, nil
	}
	ctx = s.runtime.ForTask(ctx, task)
	adapter, ok := generation.DeclarativeProtocolAdapterForContext(ctx, input.Config.InterfaceType)
	if !ok {
		return nil, "", false, nil
	}
	ack, ok := adapter.(protocol.MediaAckAdapter)
	if !ok || !ack.MediaAckAvailable() {
		return nil, "", false, nil
	}
	receipt := strings.TrimSpace(task.ProviderRequestID)
	if receipt == "" {
		return nil, "", true, errors.New("原 Bridge 图片任务回执暂不可用")
	}
	input.Config, err = s.connections.ResolveProviderConfig(input.Config)
	if err != nil {
		return nil, "", true, err
	}
	result, status, err := generation.QueryProtocolAdapterTask(ctx, input, adapter, receipt)
	if status == string(protocol.StatusFailed) || status == string(protocol.StatusCancelled) {
		return nil, localtask.RecoveryNoResult, true, nil
	}
	if err != nil || result == nil {
		return nil, "", true, err
	}
	result, err = s.completion.PersistMedia(task, result)
	if err != nil {
		return nil, "", true, err
	}
	encoded, err := json.Marshal(result)
	return encoded, "", true, err
}
