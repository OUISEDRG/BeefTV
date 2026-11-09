package app

import (
	"context"
	"errors"

	"infinite-canvas/backend/internal/generation"
	"infinite-canvas/backend/internal/mediaack"
	"infinite-canvas/backend/internal/model"
	"infinite-canvas/backend/internal/protocol"
)

func (s *Service) mediaAcks() mediaack.Sender {
	return mediaack.Sender{
		Store: s.repo, LoadedOutputs: s.generationDeliverer().LoadedOutputs,
		IsOwnedProject: s.isOwnedBusinessProject, CanvasExists: s.canvasExists, Canvas: s.UserCanvasProject,
		Conversation: func(userID, conversationID string) (*model.CreationConversation, error) {
			return s.repo.CreationConversation(nil, userID, conversationID)
		},
		OpenResource: s.OpenResource, IsDraining: s.IsDraining, ResolveProvider: s.mediaAckProvider,
	}
}

func (s *Service) mediaAckProvider(ctx context.Context, task model.Task) (context.Context, generation.Config, protocol.Adapter, error) {
	// The private command snapshot survives completion's InputJSON compaction.
	original, err := s.decryptSettingSecret(task.WorkflowSourceCipher)
	if err != nil || original == "" {
		return ctx, generation.Config{}, nil, errors.New("original provider configuration unavailable")
	}
	task.InputJSON = original
	input, err := s.providerCancellationInput(&task)
	if err != nil {
		return ctx, generation.Config{}, nil, err
	}
	task.InputJSON = mustJSON(canvasGenerationInput{Mode: input.Mode, Config: input.Config})
	ctx = withProviderRequestKind(withProviderAnalytics(ctx, s, task), "media-ack")
	ctx = withProtocolRegistry(ctx, s.protocolRegistry())
	adapter, _ := declarativeProtocolAdapterForContext(ctx, input.Config.InterfaceType)
	return ctx, input.Config, adapter, nil
}
