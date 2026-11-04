package workspace

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"regexp"
	"strings"
	"time"

	"infinite-canvas/backend/internal/outbound"
)

const maxReasoningResponseBytes = 1 << 20

var reasoningEffortName = regexp.MustCompile(`^[a-z][a-z0-9_-]{0,31}$`)

type ReasoningOption struct {
	ReasoningEffort string `json:"reasoningEffort"`
	Description     string `json:"description"`
}

type ReasoningModel struct {
	ID                        string            `json:"id"`
	Model                     string            `json:"model"`
	DisplayName               string            `json:"displayName"`
	DefaultReasoningEffort    string            `json:"defaultReasoningEffort"`
	SupportedReasoningEfforts []ReasoningOption `json:"supportedReasoningEfforts"`
}

type ModelReasoningSettings struct {
	ChannelID string            `json:"channelId"`
	Models    []ReasoningModel  `json:"models"`
	Overrides map[string]string `json:"overrides"`
	Revision  int64             `json:"revision"`
}

type ModelReasoningUpdate struct {
	ChannelID        string  `json:"channelId"`
	Model            string  `json:"model"`
	Effort           *string `json:"effort"`
	ExpectedRevision int64   `json:"expectedRevision"`
}

// ModelReasoningError contains only public, locally selected diagnostics.
type ModelReasoningError struct {
	Status  int
	Reason  string
	Message string
}

func (e *ModelReasoningError) Error() string { return e.Message }

type reasoningConfigReader interface {
	ReadLocalModelConfig() ([]byte, error)
}

type ModelReasoningService struct {
	config reasoningConfigReader
	client *http.Client
}

func NewModelReasoningService(config reasoningConfigReader) *ModelReasoningService {
	return &ModelReasoningService{config: config, client: outbound.LoopbackHTTPClient(12 * time.Second)}
}

func (s *ModelReasoningService) ReadModelReasoning(ctx context.Context, channelID string) (*ModelReasoningSettings, error) {
	return s.exchange(ctx, channelID, nil)
}

func (s *ModelReasoningService) UpdateModelReasoning(ctx context.Context, update ModelReasoningUpdate) (*ModelReasoningSettings, error) {
	if strings.TrimSpace(update.Model) == "" || strings.TrimSpace(update.Model) != update.Model || len(update.Model) > 512 || update.ExpectedRevision < 0 ||
		(update.Effort != nil && !reasoningEffortName.MatchString(*update.Effort)) {
		return nil, reasoningError(http.StatusBadRequest, "invalid_request", "推理档位设置格式错误")
	}
	return s.exchange(ctx, update.ChannelID, &update)
}

type reasoningChannel struct {
	ID            string   `json:"id"`
	Enabled       bool     `json:"enabled"`
	Scope         string   `json:"scope"`
	Pinned        bool     `json:"pinned"`
	CredentialRef string   `json:"credentialRef"`
	BaseURL       string   `json:"baseUrl"`
	APIKey        string   `json:"apiKey"`
	APIFormat     string   `json:"apiFormat"`
	InterfaceType string   `json:"interfaceType"`
	Models        []string `json:"models"`
	ModelProfiles []struct {
		Model      string `json:"model"`
		Capability string `json:"capability"`
		Protocol   string `json:"protocol"`
	} `json:"modelProfiles"`
}

func (s *ModelReasoningService) resolve(channelID string) (reasoningChannel, map[string]bool, error) {
	if channelID == "" || strings.TrimSpace(channelID) != channelID || len(channelID) > 128 {
		return reasoningChannel{}, nil, reasoningError(http.StatusBadRequest, "invalid_request", "请选择模型渠道")
	}
	body, err := s.config.ReadLocalModelConfig()
	if err != nil {
		return reasoningChannel{}, nil, reasoningError(http.StatusInternalServerError, "model_config_unavailable", "无法读取已保存的模型渠道配置")
	}
	var snapshot struct {
		Channels   []reasoningChannel `json:"channels"`
		TextModels []string           `json:"textModels"`
	}
	if json.Unmarshal(body, &snapshot) != nil {
		return reasoningChannel{}, nil, reasoningError(http.StatusInternalServerError, "model_config_unavailable", "无法读取已保存的模型渠道配置")
	}
	for _, channel := range snapshot.Channels {
		if channel.ID != channelID {
			continue
		}
		if !channel.Enabled || channel.Pinned || channel.Scope == "system" || channel.CredentialRef != "" {
			break
		}
		if _, err := outbound.ValidateLoopbackURL(channel.BaseURL); err != nil {
			break
		}
		allowed := reasoningChannelModels(channel, snapshot.TextModels)
		if len(allowed) == 0 {
			break
		}
		if strings.TrimSpace(channel.APIKey) == "" || strings.ContainsAny(channel.APIKey, "\r\n") {
			return reasoningChannel{}, nil, reasoningError(http.StatusBadRequest, "reasoning_credential_missing", "该本机渠道没有可用的连接凭据")
		}
		return channel, allowed, nil
	}
	return reasoningChannel{}, nil, reasoningError(http.StatusNotFound, "reasoning_unsupported", "该渠道未提供本机推理档位设置")
}

func reasoningChannelModels(channel reasoningChannel, textModels []string) map[string]bool {
	allowed := map[string]bool{}
	for _, modelID := range channel.Models {
		if strings.TrimSpace(modelID) == "" {
			continue
		}
		isText := false
		protocol := strings.TrimSpace(channel.InterfaceType)
		for _, selected := range textModels {
			if selected == modelID || selected == channel.ID+"::"+modelID {
				isText = true
			}
		}
		for _, profile := range channel.ModelProfiles {
			if profile.Model != modelID {
				continue
			}
			isText = profile.Capability == "text"
			if profile.Protocol != "" {
				protocol = strings.TrimSpace(profile.Protocol)
			}
			break
		}
		if protocol == "" && (channel.APIFormat == "" || channel.APIFormat == "openai") {
			protocol = "chat-completion"
		}
		if isText && protocol == "chat-completion" {
			allowed[modelID] = true
		}
	}
	return allowed
}

func (s *ModelReasoningService) exchange(ctx context.Context, channelID string, update *ModelReasoningUpdate) (*ModelReasoningSettings, error) {
	channel, allowed, err := s.resolve(channelID)
	if err != nil {
		return nil, err
	}
	endpoint, _ := outbound.ValidateLoopbackURL(channel.BaseURL)
	endpoint.Path = strings.TrimRight(endpoint.Path, "/") + "/reasoning-settings"
	endpoint.RawPath = ""
	method := http.MethodGet
	var body io.Reader
	if update != nil {
		if !allowed[update.Model] {
			return nil, reasoningError(http.StatusBadRequest, "unknown_model", "该模型不属于已保存的本机文本渠道")
		}
		method = http.MethodPut
		payload, _ := json.Marshal(struct {
			Model            string  `json:"model"`
			Effort           *string `json:"effort"`
			ExpectedRevision int64   `json:"expectedRevision"`
		}{update.Model, update.Effort, update.ExpectedRevision})
		body = bytes.NewReader(payload)
	}
	request, err := http.NewRequestWithContext(ctx, method, endpoint.String(), body)
	if err != nil {
		return nil, reasoningError(http.StatusBadRequest, "reasoning_unsupported", "本机模型服务地址无效")
	}
	request.Header.Set("Authorization", "Bearer "+channel.APIKey)
	request.Header.Set("Accept", "application/json")
	if update != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	outbound.ApplyDefaultOutboundHeaders(request)
	response, err := s.client.Do(request)
	if err != nil {
		return nil, reasoningRequestError(ctx, err)
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, maxReasoningResponseBytes+1))
	if err != nil {
		return nil, reasoningRequestError(ctx, err)
	}
	if len(raw) > maxReasoningResponseBytes {
		return nil, invalidReasoningResponse()
	}
	if response.StatusCode != http.StatusOK {
		return nil, projectReasoningError(response.StatusCode, raw)
	}
	var snapshot struct {
		Models    []ReasoningModel  `json:"models"`
		Overrides map[string]string `json:"overrides"`
		Revision  *int64            `json:"revision"`
	}
	if json.Unmarshal(raw, &snapshot) != nil || snapshot.Revision == nil || *snapshot.Revision < 0 || len(snapshot.Models) == 0 {
		return nil, invalidReasoningResponse()
	}
	result := &ModelReasoningSettings{ChannelID: channelID, Revision: *snapshot.Revision, Models: []ReasoningModel{}, Overrides: map[string]string{}}
	seen := map[string]bool{}
	for _, model := range snapshot.Models {
		if !allowed[model.ID] {
			continue
		}
		if seen[model.ID] || !validReasoningModel(model, channel.APIKey) {
			return nil, invalidReasoningResponse()
		}
		seen[model.ID] = true
		result.Models = append(result.Models, model)
		if override, found := snapshot.Overrides[model.ID]; found {
			if !hasReasoningEffort(model, override) || strings.Contains(override, channel.APIKey) {
				return nil, invalidReasoningResponse()
			}
			result.Overrides[model.ID] = override
		}
	}
	if len(result.Models) == 0 {
		return nil, reasoningError(http.StatusNotFound, "reasoning_unsupported", "该渠道没有可设置推理档位的文本模型")
	}
	return result, nil
}

func validReasoningModel(model ReasoningModel, key string) bool {
	if model.ID == "" || model.Model == "" || model.DisplayName == "" || len(model.SupportedReasoningEfforts) == 0 {
		return false
	}
	for _, value := range []string{model.ID, model.Model, model.DisplayName, model.DefaultReasoningEffort} {
		if strings.Contains(value, key) {
			return false
		}
	}
	seen := map[string]bool{}
	for _, option := range model.SupportedReasoningEfforts {
		if !reasoningEffortName.MatchString(option.ReasoningEffort) || seen[option.ReasoningEffort] || strings.Contains(option.ReasoningEffort, key) || strings.Contains(option.Description, key) {
			return false
		}
		seen[option.ReasoningEffort] = true
	}
	return seen[model.DefaultReasoningEffort]
}

func hasReasoningEffort(model ReasoningModel, effort string) bool {
	for _, option := range model.SupportedReasoningEfforts {
		if option.ReasoningEffort == effort {
			return true
		}
	}
	return false
}

func reasoningError(status int, reason, message string) *ModelReasoningError {
	return &ModelReasoningError{Status: status, Reason: reason, Message: message}
}

func reasoningRequestError(ctx context.Context, err error) error {
	if ctx.Err() != nil {
		return ctx.Err()
	}
	var timeout net.Error
	if errors.As(err, &timeout) && timeout.Timeout() {
		return reasoningError(http.StatusGatewayTimeout, "reasoning_timeout", "本机模型服务响应超时，请重试")
	}
	return reasoningError(http.StatusServiceUnavailable, "reasoning_unavailable", "无法连接本机模型服务，请确认服务已启动")
}

func invalidReasoningResponse() *ModelReasoningError {
	return reasoningError(http.StatusBadGateway, "reasoning_invalid_response", "本机模型服务返回的推理设置格式不正确")
}

func projectReasoningError(status int, raw []byte) *ModelReasoningError {
	if status == http.StatusNotFound || status == http.StatusMethodNotAllowed {
		return reasoningError(http.StatusNotFound, "reasoning_unsupported", "该本机服务未提供推理档位设置")
	}
	var envelope struct {
		Error struct {
			Code   string `json:"code"`
			Reason string `json:"reason"`
		} `json:"error"`
	}
	_ = json.Unmarshal(raw, &envelope)
	reason := envelope.Error.Reason
	if reason == "" {
		reason = envelope.Error.Code
	}
	if status == http.StatusConflict {
		return reasoningError(status, "settings_conflict", "推理设置已被修改，请刷新后重试")
	}
	switch reason {
	case "unsupported_reasoning":
		return reasoningError(http.StatusBadRequest, reason, "该模型不支持所选推理档位")
	case "unknown_model":
		return reasoningError(http.StatusBadRequest, reason, "本机模型目录中没有该模型，请刷新后重试")
	case "invalid_request", "invalid_settings":
		return reasoningError(http.StatusBadRequest, reason, "本机模型服务拒绝了此设置，请刷新后重试")
	}
	if status == http.StatusUnauthorized || status == http.StatusForbidden {
		return reasoningError(http.StatusBadGateway, "reasoning_auth_failed", "本机模型服务连接凭据无效，请检查渠道配置")
	}
	return reasoningError(http.StatusBadGateway, "reasoning_upstream_error", "本机模型服务暂时无法处理推理设置")
}
