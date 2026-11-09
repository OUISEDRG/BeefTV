package protocol

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"maps"
	"regexp"
	"strings"
	"time"
)

type MediaArtifact struct {
	SchemaVersion int    `json:"schemaVersion"`
	TaskID        string `json:"taskId"`
	ArtifactID    string `json:"artifactId"`
	OutputIndex   int    `json:"outputIndex"`
	SHA256        string `json:"sha256"`
	SizeBytes     int64  `json:"sizeBytes"`
	MIMEType      string `json:"mimeType"`
}

type MediaAck struct {
	SchemaVersion  int       `json:"schemaVersion"`
	TaskID         string    `json:"taskId"`
	ArtifactID     string    `json:"artifactId"`
	ResourceID     string    `json:"resourceId"`
	OutputIndex    int       `json:"outputIndex"`
	SHA256         string    `json:"sha256"`
	SizeBytes      int64     `json:"sizeBytes"`
	AcknowledgedAt time.Time `json:"acknowledgedAt"`
}

type MediaAckAdapter interface {
	MediaAckAvailable() bool
	BuildMediaAck(context.Context, PollContext, MediaAck, string) (RequestSpec, error)
}

var mediaSHA256 = regexp.MustCompile(`^[0-9a-f]{64}$`)

func (a MediaArtifact) MatchesBytes(mime string, data []byte) bool {
	sum := sha256.Sum256(data)
	return a.MIMEType == mime && a.SizeBytes == int64(len(data)) && a.SHA256 == hex.EncodeToString(sum[:])
}

func ValidateMediaArtifacts(artifacts []MediaArtifact, taskID string, count int) error {
	if strings.TrimSpace(taskID) == "" || len(artifacts) != count {
		return errors.New("media artifact count or task identity is invalid")
	}
	seen := make(map[string]bool, count)
	for index, artifact := range artifacts {
		if artifact.SchemaVersion != 1 || artifact.TaskID != taskID || artifact.OutputIndex != index ||
			strings.TrimSpace(artifact.ArtifactID) == "" || len(artifact.ArtifactID) > 200 || seen[artifact.ArtifactID] ||
			artifact.SizeBytes <= 0 || !mediaSHA256.MatchString(artifact.SHA256) {
			return fmt.Errorf("media artifact %d identity or byte descriptor is invalid", index)
		}
		seen[artifact.ArtifactID] = true
		switch artifact.MIMEType {
		case "image/png", "image/jpeg", "image/webp", "video/mp4", "audio/mpeg", "audio/wav", "audio/ogg":
		default:
			return fmt.Errorf("media artifact %d format is unsupported", index)
		}
	}
	return nil
}

func decodeMediaArtifacts(expression any, env map[string]any) ([]MediaArtifact, error) {
	if expression == nil {
		return nil, nil
	}
	value, err := evaluateManifestValue(expression, env)
	if err != nil || value == nil {
		return nil, err
	}
	encoded, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	var artifacts []MediaArtifact
	decoder := json.NewDecoder(bytes.NewReader(encoded))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&artifacts); err != nil {
		return nil, fmt.Errorf("invalid media artifact descriptors: %w", err)
	}
	return artifacts, nil
}

func (a manifestAdapter) MediaAckAvailable() bool { return a.manifest.Ack != nil }

func (a manifestAdapter) BuildMediaAck(_ context.Context, c PollContext, ack MediaAck, key string) (RequestSpec, error) {
	if !a.MediaAckAvailable() || strings.TrimSpace(c.TaskID) == "" || strings.TrimSpace(key) == "" {
		return RequestSpec{}, errors.New("media acknowledgement requires a declared operation and original provider receipt")
	}
	request := c.Request
	request.Model = c.Model
	request.Extra = make(map[string]any, len(c.Request.Extra)+2)
	maps.Copy(request.Extra, c.Request.Extra)
	request.Extra["mediaAck"] = ack
	request.Extra["mediaAckKey"] = key
	return a.buildOperation(*a.manifest.Ack, request, c.TaskID)
}

func (a metadataAdapter) MediaAckAvailable() bool {
	adapter, ok := a.delegate.(MediaAckAdapter)
	return ok && adapter.MediaAckAvailable()
}

func (a metadataAdapter) BuildMediaAck(ctx context.Context, c PollContext, ack MediaAck, key string) (RequestSpec, error) {
	adapter, ok := a.delegate.(MediaAckAdapter)
	if !ok {
		return RequestSpec{}, errors.New("media acknowledgement is not supported")
	}
	return adapter.BuildMediaAck(ctx, c, ack, key)
}
