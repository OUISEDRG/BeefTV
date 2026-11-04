package workspace

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"infinite-canvas/backend/internal/outbound"
)

type ModelVoice struct {
	ID         string `json:"id"`
	Name       string `json:"name"`
	Source     string `json:"source"`
	PreviewURL string `json:"previewUrl"`
}
type ModelVoiceCatalog struct {
	ChannelID string       `json:"channelId"`
	Voices    []ModelVoice `json:"voices"`
}
type ModelVoiceCatalogError struct {
	Status          int
	Reason, Message string
}

func (e *ModelVoiceCatalogError) Error() string { return e.Message }
func voiceCatalogError(status int, reason, message string) error {
	return &ModelVoiceCatalogError{status, reason, message}
}

// Catalog credentials come only from saved workspace channels, never the request.
type ModelVoiceCatalogService struct {
	config interface{ ReadLocalModelConfig() ([]byte, error) }
	client *http.Client
}

func NewModelVoiceCatalogService(config interface{ ReadLocalModelConfig() ([]byte, error) }) *ModelVoiceCatalogService {
	return &ModelVoiceCatalogService{config: config, client: outbound.LoopbackHTTPClient(30 * time.Second)}
}
func (s *ModelVoiceCatalogService) ReadModelVoices(ctx context.Context, channelID string) (*ModelVoiceCatalog, error) {
	if channelID == "" || len(channelID) > 128 || strings.TrimSpace(channelID) != channelID {
		return nil, voiceCatalogError(400, "invalid_request", "请选择音频渠道")
	}
	raw, err := s.config.ReadLocalModelConfig()
	if err != nil {
		return nil, voiceCatalogError(500, "model_config_unavailable", "无法读取模型配置")
	}
	var config struct {
		Channels []reasoningChannel `json:"channels"`
	}
	if json.Unmarshal(raw, &config) != nil {
		return nil, voiceCatalogError(500, "model_config_unavailable", "无法读取模型配置")
	}
	var selected *reasoningChannel
	for _, channel := range config.Channels {
		if channel.ID != channelID {
			continue
		}
		if selected != nil {
			return nil, voiceCatalogError(400, "invalid_request", "模型渠道配置重复")
		}
		copy := channel
		selected = &copy
	}
	if selected == nil || !selected.Enabled || selected.Pinned || selected.Scope == "system" || selected.CredentialRef != "" {
		return nil, voiceCatalogError(404, "voices_unsupported", "该渠道没有本机音色目录")
	}
	channel := *selected
	hasAudio := false
	for _, p := range channel.ModelProfiles {
		for _, m := range channel.Models {
			if p.Model == m && p.Capability == "audio" {
				hasAudio = true
			}
		}
	}
	if !hasAudio {
		return nil, voiceCatalogError(404, "voices_unsupported", "请选择本机音频渠道")
	}
	endpoint, err := outbound.ValidateLoopbackURL(channel.BaseURL)
	if err != nil {
		return nil, voiceCatalogError(400, "voices_unsupported", "音色目录仅支持已保存的本机渠道")
	}
	if strings.TrimSpace(channel.APIKey) == "" || strings.ContainsAny(channel.APIKey, "\r\n") {
		return nil, voiceCatalogError(400, "voices_credential_missing", "本机音频渠道没有连接凭据")
	}
	basePath := strings.TrimRight(endpoint.Path, "/")
	if !strings.HasSuffix(basePath, "/v1") {
		basePath += "/v1"
	}
	endpoint.Path = basePath + "/voices"
	endpoint.RawPath = ""
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint.String(), nil)
	if err != nil {
		return nil, voiceCatalogError(400, "voices_unsupported", "本机音频地址无效")
	}
	request.Header.Set("Authorization", "Bearer "+channel.APIKey)
	request.Header.Set("Accept", "application/json")
	outbound.ApplyDefaultOutboundHeaders(request)
	response, err := s.client.Do(request)
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, voiceCatalogError(503, "voices_unavailable", "无法读取音色目录，请确认剧梦桥接服务和登录可用")
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return nil, voiceCatalogError(502, "voices_upstream_error", "音色目录读取失败，请检查剧梦登录和桥接服务")
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 2<<20+1))
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	invalid := func() (*ModelVoiceCatalog, error) {
		return nil, voiceCatalogError(502, "voices_invalid_response", "本机音色目录格式不正确")
	}
	if err != nil || len(body) > 2<<20 {
		return invalid()
	}
	var result struct {
		Voices []ModelVoice `json:"voices"`
	}
	if json.Unmarshal(body, &result) != nil || result.Voices == nil || len(result.Voices) > 10000 {
		return invalid()
	}
	seen := map[string]bool{}
	for _, voice := range result.Voices {
		if voice.ID == "" || len(voice.ID) > 128 || strings.TrimSpace(voice.ID) != voice.ID || strings.TrimSpace(voice.Name) == "" || len(voice.Name) > 1024 || seen[voice.ID] || (voice.Source != "public" && voice.Source != "personal") {
			return invalid()
		}
		seen[voice.ID] = true
		for _, v := range []string{voice.ID, voice.Name, voice.Source, voice.PreviewURL} {
			if strings.Contains(v, channel.APIKey) {
				return invalid()
			}
		}
		if voice.PreviewURL != "" {
			preview, e := url.Parse(voice.PreviewURL)
			if e != nil || preview.Scheme != "https" || preview.User != nil || preview.Hostname() == "" || preview.Fragment != "" || len(voice.PreviewURL) > 4096 {
				return invalid()
			}
		}
	}
	return &ModelVoiceCatalog{ChannelID: channelID, Voices: result.Voices}, nil
}
