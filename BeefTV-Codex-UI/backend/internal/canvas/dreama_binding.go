package canvas

import (
	"encoding/json"
	"reflect"
	"strings"

	"infinite-canvas/backend/internal/kernel"
)

type dreamaCanvasBinding struct {
	SchemaVersion   int    `json:"schemaVersion"`
	ProjectID       int64  `json:"projectId"`
	ProjectName     string `json:"projectName"`
	PromptMode      string `json:"promptMode"`
	AspectRatio     string `json:"aspectRatio"`
	DefaultSeriesID int64  `json:"defaultSeriesId"`
	Revision        int64  `json:"revision"`
}

func readDreamaBinding(raw json.RawMessage) (*dreamaCanvasBinding, error) {
	if len(raw) == 0 {
		return nil, nil
	}
	var document struct {
		Integrations map[string]json.RawMessage `json:"integrations"`
	}
	if json.Unmarshal(raw, &document) != nil {
		return nil, kernel.BadAuthRequest("画布集成配置无效")
	}
	value := document.Integrations["dreama"]
	if len(value) == 0 || string(value) == "null" {
		return nil, nil
	}
	var binding dreamaCanvasBinding
	if json.Unmarshal(value, &binding) != nil || binding.SchemaVersion != 1 || binding.ProjectID <= 0 || binding.DefaultSeriesID <= 0 || binding.Revision < 1 || binding.PromptMode != "drama" || strings.TrimSpace(binding.AspectRatio) == "" || len(binding.AspectRatio) > 32 || len(binding.ProjectName) > 1024 {
		return nil, kernel.BadAuthRequest("剧梦画布绑定格式无效，请重新选择项目与默认剧集")
	}
	return &binding, nil
}
func validateDreamaBindingChange(previous, current json.RawMessage) error {
	next, err := readDreamaBinding(current)
	if err != nil {
		return err
	}
	old, err := readDreamaBinding(previous)
	if err != nil {
		return err
	}
	if old == nil {
		return nil
	}
	if next == nil || next.ProjectID != old.ProjectID {
		return kernel.BadAuthRequest("已绑定画布不能直接更换或清除剧梦项目，请复制画布后重新绑定")
	}
	if next.Revision < old.Revision || (!reflect.DeepEqual(old, next) && next.Revision <= old.Revision) {
		return kernel.BadAuthRequest("剧梦绑定版本已变化，请重新加载画布")
	}
	return nil
}
