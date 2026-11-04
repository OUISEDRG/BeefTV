// Package dreamaintegration connects the local workspace to a saved Dreama
// bridge. It never owns generation execution or accepts browser credentials.
package dreamaintegration

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"

	"infinite-canvas/backend/internal/outbound"
)

type ConfigReader interface{ ReadLocalModelConfig() ([]byte, error) }
type revisionWriter interface {
	SaveLocalModelConfigRevision([]byte, int64) (int64, error)
}
type Error struct {
	Status  int
	Reason  string
	Message string
}

func (e *Error) Error() string                         { return e.Message }
func problem(status int, reason, message string) error { return &Error{status, reason, message} }

type Service struct {
	config ConfigReader
	client *http.Client
	launch func(RuntimeConfig) error
}

func New(config ConfigReader) *Service {
	return &Service{config: config, client: outbound.LoopbackHTTPClient(30 * time.Second)}
}

type channel struct {
	ID            string `json:"id"`
	Enabled       bool   `json:"enabled"`
	Scope         string `json:"scope"`
	Pinned        bool   `json:"pinned"`
	CredentialRef string `json:"credentialRef"`
	APIKey        string `json:"apiKey"`
	BaseURL       string `json:"baseUrl"`
	ModelProfiles []struct {
		Protocol string `json:"protocol"`
	} `json:"modelProfiles"`
}

func dreamaChannel(c channel) bool {
	if !c.Enabled || c.Pinned || c.Scope == "system" || c.CredentialRef != "" {
		return false
	}
	for _, p := range c.ModelProfiles {
		if strings.HasPrefix(p.Protocol, "dreama-") || (c.ID == "dreama-video-enhancement" && p.Protocol == "seedance-videos-compatible") {
			return true
		}
	}
	return false
}
func (s *Service) resolve(id string) (channel, *url.URL, error) {
	invalid := func() (channel, *url.URL, error) {
		return channel{}, nil, problem(400, "dreama_channel_invalid", "请选择已保存的本机剧梦渠道")
	}
	if id == "" || len(id) > 128 || strings.TrimSpace(id) != id {
		return invalid()
	}
	raw, err := s.config.ReadLocalModelConfig()
	if err != nil {
		return channel{}, nil, problem(503, "model_config_unavailable", "无法读取模型渠道配置")
	}
	var config struct {
		Channels []channel `json:"channels"`
	}
	if json.Unmarshal(raw, &config) != nil {
		return invalid()
	}
	var selected *channel
	for _, candidate := range config.Channels {
		if candidate.ID == id {
			if selected != nil {
				return invalid()
			}
			copy := candidate
			selected = &copy
		}
	}
	if selected == nil || !dreamaChannel(*selected) || strings.TrimSpace(selected.APIKey) == "" || strings.ContainsAny(selected.APIKey, "\r\n") {
		return invalid()
	}
	endpoint, err := outbound.ValidateLoopbackURL(selected.BaseURL)
	if err != nil {
		return invalid()
	}
	endpoint.Path = ""
	endpoint.RawPath = ""
	return *selected, endpoint, nil
}

var safeTaskID = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,150}$`)

func validRoute(method, path string, query map[string]string) bool {
	allowed := map[string]bool{}
	if method == http.MethodGet {
		switch path {
		case "status", "projects":
		case "catalog":
			allowed["projectId"] = true
		case "series":
			allowed["projectId"] = true
			if query["projectId"] == "" {
				return false
			}
		case "tasks":
			allowed["canvasId"] = true
		default:
			return false
		}
	} else if method == http.MethodPost {
		if path != "series" {
			parts := strings.Split(path, "/")
			if len(parts) != 3 || parts[0] != "tasks" || !safeTaskID.MatchString(parts[1]) || parts[2] != "cancel" {
				return false
			}
		}
	} else {
		return false
	}
	for key, value := range query {
		if !allowed[key] || value == "" || len(value) > 200 || strings.ContainsAny(value, "\x00\r\n") {
			return false
		}
		if key == "projectId" {
			id, err := strconv.ParseInt(value, 10, 64)
			if err != nil || id <= 0 {
				return false
			}
		}
	}
	return true
}
func (s *Service) Exchange(ctx context.Context, channelID, method, path string, query map[string]string, body json.RawMessage) (json.RawMessage, error) {
	if !validRoute(method, path, query) {
		return nil, problem(400, "invalid_request", "剧梦工作台请求格式无效")
	}
	selected, endpoint, err := s.resolve(channelID)
	if err != nil {
		return nil, err
	}
	if method == http.MethodPost {
		if len(body) == 0 {
			body = json.RawMessage(`{}`)
		}
		var fields map[string]json.RawMessage
		if len(body) > 16384 || json.Unmarshal(body, &fields) != nil || fields == nil {
			return nil, problem(400, "invalid_request", "剧梦工作台请求格式无效")
		}
		for key := range fields {
			if path != "series" || (key != "projectId" && key != "name") {
				return nil, problem(400, "invalid_request", "剧梦工作台请求包含未知字段")
			}
		}
	}
	endpoint.Path = "/integration/v1/" + path
	params := url.Values{}
	for key, value := range query {
		params.Set(key, value)
	}
	endpoint.RawQuery = params.Encode()
	request, err := http.NewRequestWithContext(ctx, method, endpoint.String(), bytes.NewReader(body))
	if err != nil {
		return nil, problem(400, "invalid_request", "剧梦工作台地址无效")
	}
	request.Header.Set("Authorization", "Bearer "+selected.APIKey)
	request.Header.Set("Accept", "application/json")
	if method == http.MethodPost {
		request.Header.Set("Content-Type", "application/json")
	}
	response, err := s.client.Do(request)
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, problem(503, "dreama_bridge_unavailable", "剧梦桥接服务未就绪")
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		status := http.StatusBadGateway
		reason := "dreama_bridge_error"
		message := "剧梦请求失败，请检查登录、任务状态或桥接版本"
		if response.StatusCode == 409 {
			status = 409
			reason = "dreama_task_conflict"
			message = "任务已进入提交阶段，当前操作不能执行"
		}
		if response.StatusCode == 401 {
			reason = "dreama_auth_required"
			message = "剧梦连接认证失败，请检查本机桥接配置和登录"
		}
		if response.StatusCode == 404 {
			reason = "dreama_integration_unavailable"
			message = "剧梦集成接口不可用，请检查桥接版本或任务是否存在"
		}
		return nil, problem(status, reason, message)
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, 4<<20+1))
	var object map[string]json.RawMessage
	if err != nil || len(data) > 4<<20 || json.Unmarshal(data, &object) != nil || object == nil || bytes.Contains(data, []byte(selected.APIKey)) {
		return nil, problem(502, "dreama_invalid_response", "剧梦桥接响应格式无效")
	}
	return data, nil
}

type RefreshResult struct {
	Revision        int64    `json:"revision"`
	UpdatedChannels []string `json:"updatedChannels"`
}

func (s *Service) Refresh(ctx context.Context, channelID string, expectedRevision int64) (*RefreshResult, error) {
	writer, ok := s.config.(revisionWriter)
	if !ok || expectedRevision < 0 {
		return nil, problem(409, "model_config_version_required", "刷新目录需要支持版本检查的模型配置")
	}
	selected, origin, err := s.resolve(channelID)
	if err != nil {
		return nil, err
	}
	data, err := s.Exchange(ctx, channelID, "GET", "catalog", nil, nil)
	if err != nil {
		return nil, err
	}
	var catalog struct {
		SchemaVersion int `json:"schemaVersion"`
		Channels      []struct {
			ID       string           `json:"id"`
			Models   []string         `json:"models"`
			Profiles []map[string]any `json:"modelProfiles"`
		} `json:"channels"`
	}
	if json.Unmarshal(data, &catalog) != nil || catalog.SchemaVersion != 1 || len(catalog.Channels) == 0 || len(catalog.Channels) > 32 {
		return nil, problem(502, "dreama_invalid_catalog", "剧梦能力目录格式无效")
	}
	raw, err := s.config.ReadLocalModelConfig()
	if err != nil {
		return nil, err
	}
	var config map[string]json.RawMessage
	if json.Unmarshal(raw, &config) != nil {
		return nil, problem(503, "model_config_unavailable", "模型配置不可用")
	}
	var channels []map[string]json.RawMessage
	if json.Unmarshal(config["channels"], &channels) != nil {
		return nil, problem(503, "model_config_unavailable", "模型渠道配置不可用")
	}
	result := &RefreshResult{UpdatedChannels: []string{}}
	seen := map[string]bool{}
	for _, fresh := range catalog.Channels {
		if fresh.ID == "" || seen[fresh.ID] || len(fresh.Models) > 2000 || len(fresh.Profiles) != len(fresh.Models) {
			return nil, problem(502, "dreama_invalid_catalog", "剧梦能力目录型号重复或不完整")
		}
		seen[fresh.ID] = true
		models := map[string]bool{}
		for _, id := range fresh.Models {
			if id == "" || models[id] {
				return nil, problem(502, "dreama_invalid_catalog", "剧梦能力目录型号重复")
			}
			models[id] = true
		}
		profiles := map[string]bool{}
		for _, profile := range fresh.Profiles {
			id, _ := profile["model"].(string)
			protocol, _ := profile["protocol"].(string)
			if !models[id] || profiles[id] || !strings.HasPrefix(protocol, "dreama-") {
				return nil, problem(502, "dreama_invalid_catalog", "剧梦能力目录协议无效")
			}
			profiles[id] = true
		}
		matched := false
		for _, existing := range channels {
			encoded, _ := json.Marshal(existing)
			var item channel
			json.Unmarshal(encoded, &item)
			if item.ID != fresh.ID {
				continue
			}
			matched = true
			if !item.Enabled || item.Pinned || item.Scope == "system" || item.CredentialRef != "" {
				continue
			}
			endpoint, e := outbound.ValidateLoopbackURL(item.BaseURL)
			if !dreamaChannel(item) || e != nil || endpoint.Scheme != origin.Scheme || endpoint.Host != origin.Host || item.APIKey != selected.APIKey {
				return nil, problem(409, "dreama_channel_mismatch", "目录不能覆盖其他连接或渠道")
			}
			existing["models"], _ = json.Marshal(fresh.Models)
			existing["modelProfiles"], _ = json.Marshal(fresh.Profiles)
			result.UpdatedChannels = append(result.UpdatedChannels, fresh.ID)
		}
		if !matched && !strings.HasPrefix(fresh.ID, "dreama-") {
			return nil, problem(502, "dreama_invalid_catalog", "目录包含非剧梦渠道")
		}
	}
	if len(result.UpdatedChannels) == 0 {
		return nil, problem(409, "dreama_channel_mismatch", "未找到可刷新的已保存剧梦渠道")
	}
	config["channels"], _ = json.Marshal(channels)
	updated, _ := json.Marshal(config)
	revision, err := writer.SaveLocalModelConfigRevision(updated, expectedRevision)
	if err != nil {
		return nil, err
	}
	result.Revision = revision
	return result, nil
}
