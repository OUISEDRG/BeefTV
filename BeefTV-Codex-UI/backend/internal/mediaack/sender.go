package mediaack

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"slices"
	"strings"
	"time"

	"infinite-canvas/backend/internal/assets"
	"infinite-canvas/backend/internal/generation"
	"infinite-canvas/backend/internal/model"
	"infinite-canvas/backend/internal/protocol"
	localtask "infinite-canvas/backend/internal/task"
	"infinite-canvas/backend/internal/taskbinding"
)

type Store interface {
	Task(string) (*model.Task, error)
	MediaDeliveryAckIndexes(string) ([]int, error)
	EnqueueMediaDeliveryAck(model.MediaDeliveryAck) error
	DueMediaDeliveryAcks(time.Time, int) ([]model.MediaDeliveryAck, error)
	ClaimMediaDeliveryAck(model.MediaDeliveryAck, time.Time, time.Time) (bool, error)
	FinishMediaDeliveryAck(model.MediaDeliveryAck, string, *time.Time) error
	MediaBindingReceipt(userID, opID, op string) (string, error)
	HasRegisteredWorkflowOutput(model.Task, string, string) (bool, error)
}

type Sender struct {
	Store           Store
	LoadedOutputs   func(string) ([]localtask.CanonicalOutput, error)
	IsOwnedProject  func(string) func(string) (bool, error)
	CanvasExists    func(string) func(string) (bool, error)
	Canvas          func(string, string) (json.RawMessage, error)
	Conversation    func(string, string) (*model.CreationConversation, error)
	OpenResource    func(string, string) (*model.Resource, io.ReadCloser, error)
	IsDraining      func() bool
	ResolveProvider func(context.Context, model.Task) (context.Context, generation.Config, protocol.Adapter, error)
}

type mediaDeliveryResult struct {
	Artifacts         []protocol.MediaArtifact `json:"deliveryArtifacts"`
	ProviderRequestID string                   `json:"deliveryProviderRequestId"`
	Route             string                   `json:"deliveryRoute"`
}

func taskMediaDelivery(task model.Task) (*mediaDeliveryResult, error) {
	if task.Status != model.TaskStatusSucceeded || !strings.Contains(task.ResultJSON, `"deliveryArtifacts"`) {
		return nil, nil
	}
	var delivery mediaDeliveryResult
	if err := json.Unmarshal([]byte(task.ResultJSON), &delivery); err != nil {
		return nil, err
	}
	if len(delivery.Artifacts) == 0 || delivery.ProviderRequestID == "" || delivery.ProviderRequestID != task.ProviderRequestID || delivery.Route == "" {
		return nil, errors.New("media delivery does not match the original provider route and receipt")
	}
	if err := protocol.ValidateMediaArtifacts(delivery.Artifacts, task.ID, len(delivery.Artifacts)); err != nil {
		return nil, err
	}
	return &delivery, nil
}

func (s Sender) Queue(task model.Task) error {
	delivery, err := taskMediaDelivery(task)
	if err != nil || delivery == nil {
		return err
	}
	queued, err := s.Store.MediaDeliveryAckIndexes(task.ID)
	if err != nil {
		return err
	}
	outputs, err := s.LoadedOutputs(task.ID)
	if err != nil {
		return err
	}
	for _, output := range outputs {
		index := output.OutputIndex
		if index < 0 || index >= len(delivery.Artifacts) || slices.Contains(queued, index) {
			continue
		}
		artifact := delivery.Artifacts[index]
		if !s.mediaDeliveryReady(task, outputs, output, artifact) {
			continue
		}
		now := time.Now().UTC()
		ack := protocol.MediaAck{SchemaVersion: 1, TaskID: task.ID, ArtifactID: artifact.ArtifactID,
			ResourceID: output.ResourceID, OutputIndex: index, SHA256: artifact.SHA256, SizeBytes: artifact.SizeBytes, AcknowledgedAt: now}
		payload, err := json.Marshal(ack)
		if err != nil {
			return err
		}
		identity := sha256.Sum256([]byte(task.ID + "\x00" + delivery.ProviderRequestID + "\x00" + artifact.ArtifactID))
		if err := s.Store.EnqueueMediaDeliveryAck(model.MediaDeliveryAck{TaskID: task.ID, OutputIndex: index,
			PayloadJSON: string(payload), IdempotencyKey: fmt.Sprintf("beeftv-ack-%x", identity), NextAttemptAt: now}); err != nil {
			return err
		}
	}
	return nil
}

func (s Sender) mediaDeliveryReady(task model.Task, outputs []localtask.CanonicalOutput, output localtask.CanonicalOutput, artifact protocol.MediaArtifact) bool {
	if output.ResourceID == "" || output.MaterializedAssetID == "" || output.MaterializationErrorCode != "" {
		return false
	}
	intents := localtask.CanvasBindingIntents(task.ID, outputs)
	canvasIDs, missing, err := localtask.TaskCanvasState(task, intents, s.IsOwnedProject(task.UserID), s.CanvasExists(task.UserID))
	if err != nil || missing {
		return false
	}
	if stepID := localtask.WorkflowOutputStepID(task.InputJSON); stepID != "" {
		bound, err := s.Store.HasRegisteredWorkflowOutput(task, stepID, output.ResourceID)
		if err != nil || !bound {
			return false
		}
	}
	canvasBound := false
	for _, target := range localtask.CanvasBindTargets(task, intents) {
		if target.OutputIndex != artifact.OutputIndex || len(canvasIDs) == 0 {
			continue
		}
		raw, err := s.Store.MediaBindingReceipt(task.UserID, localtask.AttachNodeEffectKey(task.ID, target.NodeID, target.OutputIndex), "canvas.task.bind")
		var receipt taskbinding.Receipt
		if err != nil || !decodeMediaBindingReceipt(raw, &receipt) || !(receipt.Applied || receipt.AlreadyBound) ||
			receipt.CanvasID != task.ProjectID || receipt.TaskID != task.ID || receipt.NodeID != target.NodeID ||
			receipt.OutputIndex != artifact.OutputIndex || receipt.ResourceID != output.ResourceID ||
			!s.mediaCanvasBindingMatches(task, target, output.ResourceID) {
			return false
		}
		canvasBound = true
	}
	if len(canvasIDs) > 0 && !canvasBound {
		return false
	}
	for _, target := range localtask.MessageBindTargets(task, intents) {
		if target.OutputIndex != artifact.OutputIndex {
			continue
		}
		raw, err := s.Store.MediaBindingReceipt(task.UserID, localtask.AttachMessageEffectKey(task.ID, target.MessageID, target.OutputIndex), "conversation.message.attach")
		var receipt taskbinding.MessageReceipt
		if err != nil || !decodeMediaBindingReceipt(raw, &receipt) || !(receipt.Applied || receipt.AlreadyBound) ||
			receipt.TaskID != task.ID || receipt.ConversationID != target.ConversationID || receipt.MessageID != target.MessageID ||
			receipt.OutputIndex != artifact.OutputIndex || receipt.ResourceID != output.ResourceID ||
			!s.mediaMessageBindingMatches(task, target, output.ResourceID) {
			return false
		}
	}
	resource, body, err := s.OpenResource(task.UserID, output.ResourceID)
	if err != nil {
		return false
	}
	defer body.Close()
	if resource.Status != model.ResourceStatusReady || resource.ID != output.ResourceID || resource.UserID != task.UserID ||
		resource.Size != artifact.SizeBytes || resource.MimeType != artifact.MIMEType {
		return false
	}
	hash := sha256.New()
	size, err := io.Copy(hash, io.LimitReader(body, artifact.SizeBytes+1))
	return err == nil && size == artifact.SizeBytes && fmt.Sprintf("%x", hash.Sum(nil)) == artifact.SHA256
}

func (s Sender) mediaCanvasBindingMatches(task model.Task, target localtask.CanvasBindTarget, resourceID string) bool {
	raw, err := s.Canvas(task.UserID, task.ProjectID)
	var canvas struct {
		Nodes []struct {
			ID       string `json:"id"`
			Metadata struct {
				TaskID     string   `json:"taskId"`
				StorageKey string   `json:"storageKey"`
				EffectKeys []string `json:"generationEffectKeys"`
			} `json:"metadata"`
		} `json:"nodes"`
	}
	if err != nil || json.Unmarshal(raw, &canvas) != nil {
		return false
	}
	effect := localtask.AttachNodeEffectKey(task.ID, target.NodeID, target.OutputIndex)
	for _, node := range canvas.Nodes {
		if node.ID == target.NodeID {
			return node.Metadata.TaskID == task.ID && node.Metadata.StorageKey == "resource:"+resourceID && slices.Contains(node.Metadata.EffectKeys, effect)
		}
	}
	return false
}

func (s Sender) mediaMessageBindingMatches(task model.Task, target localtask.MessageBindTarget, resourceID string) bool {
	row, err := s.Conversation(task.UserID, target.ConversationID)
	var conversation struct {
		Messages []struct {
			ID         string   `json:"id"`
			TaskIDs    []string `json:"taskIds"`
			ResultURLs []string `json:"resultUrls"`
			EffectKeys []string `json:"generationEffectKeys"`
		} `json:"messages"`
	}
	if err != nil || row.Deleted || json.Unmarshal([]byte(row.Document), &conversation) != nil {
		return false
	}
	effect := localtask.AttachMessageEffectKey(task.ID, target.MessageID, target.OutputIndex)
	for _, message := range conversation.Messages {
		if message.ID == target.MessageID {
			return slices.Contains(message.TaskIDs, task.ID) && slices.Contains(message.ResultURLs, assets.FileURL(resourceID)) && slices.Contains(message.EffectKeys, effect)
		}
	}
	return false
}

func decodeMediaBindingReceipt(raw string, target any) bool {
	var projected struct {
		BindingStatus string          `json:"bindingStatus"`
		Historical    json.RawMessage `json:"historical"`
	}
	if err := json.Unmarshal([]byte(raw), &projected); err != nil || (projected.BindingStatus != "" && projected.BindingStatus != "bound") {
		return false
	}
	if len(projected.Historical) != 0 {
		return json.Unmarshal(projected.Historical, target) == nil
	}
	return json.Unmarshal([]byte(raw), target) == nil
}

func (s Sender) Run(ctx context.Context, interval time.Duration) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for ctx.Err() == nil {
		if err := s.Process(ctx); err != nil {
			log.Printf("media acknowledgement recovery deferred: %v", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func (s Sender) Process(ctx context.Context) error {
	rows, err := s.Store.DueMediaDeliveryAcks(time.Now().UTC(), 16)
	if err != nil {
		return err
	}
	for _, row := range rows {
		if ctx.Err() != nil || s.IsDraining() {
			return nil
		}
		now := time.Now().UTC()
		claimed, err := s.Store.ClaimMediaDeliveryAck(row, now, now.Add(30*time.Second))
		if err != nil {
			return err
		}
		if !claimed {
			continue
		}
		reason := s.sendMediaDeliveryAck(ctx, row)
		var confirmed *time.Time
		if reason == "" {
			now := time.Now().UTC()
			confirmed = &now
		}
		if err := s.Store.FinishMediaDeliveryAck(row, reason, confirmed); err != nil {
			return err
		}
	}
	return nil
}

func (s Sender) sendMediaDeliveryAck(ctx context.Context, row model.MediaDeliveryAck) string {
	task, err := s.Store.Task(row.TaskID)
	if err != nil {
		return "original_task_unavailable"
	}
	delivery, err := taskMediaDelivery(*task)
	if err != nil || delivery == nil || row.OutputIndex < 0 || row.OutputIndex >= len(delivery.Artifacts) {
		return "delivery_identity_unavailable"
	}
	artifact := delivery.Artifacts[row.OutputIndex]
	var ack protocol.MediaAck
	if err := json.Unmarshal([]byte(row.PayloadJSON), &ack); err != nil || ack.TaskID != task.ID || ack.ArtifactID != artifact.ArtifactID ||
		ack.OutputIndex != artifact.OutputIndex || ack.SHA256 != artifact.SHA256 || ack.SizeBytes != artifact.SizeBytes {
		return "delivery_identity_mismatch"
	}
	outputs, err := s.LoadedOutputs(task.ID)
	if err != nil {
		return "local_delivery_unavailable"
	}
	outputIndex := slices.IndexFunc(outputs, func(output localtask.CanonicalOutput) bool {
		return output.OutputIndex == ack.OutputIndex && output.ResourceID == ack.ResourceID
	})
	if outputIndex < 0 || !s.mediaDeliveryReady(*task, outputs, outputs[outputIndex], artifact) {
		return "local_delivery_unverified"
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	ctx, config, adapter, err := s.ResolveProvider(ctx, *task)
	if err != nil {
		return "provider_configuration_unavailable"
	}
	if adapter == nil || !adapter.Metadata().Enabled || generation.MediaDeliveryRoute(config, adapter) != delivery.Route {
		return "provider_route_changed"
	}
	acknowledger, ok := adapter.(protocol.MediaAckAdapter)
	if !ok || !acknowledger.MediaAckAvailable() {
		return "provider_ack_unavailable"
	}
	spec, err := acknowledger.BuildMediaAck(ctx, protocol.PollContext{BaseURL: config.BaseURL,
		Model: config.Model, TaskID: delivery.ProviderRequestID}, ack, row.IdempotencyKey)
	if err != nil {
		return "provider_ack_unavailable"
	}
	data, err := generation.ExecuteProtocolRequest(ctx, config, spec)
	if err != nil {
		return "provider_ack_retry"
	}
	var receipt struct {
		Acknowledged bool   `json:"acknowledged"`
		ArtifactID   string `json:"artifactId"`
	}
	if err := json.Unmarshal(data, &receipt); err != nil || !receipt.Acknowledged || receipt.ArtifactID != ack.ArtifactID {
		return "provider_ack_unconfirmed"
	}
	return ""
}
