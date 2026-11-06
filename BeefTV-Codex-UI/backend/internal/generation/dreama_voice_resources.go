package generation

import (
	"context"
	"errors"
	"fmt"
	"io"
	"maps"
	"mime"
	"regexp"
	"strings"
)

const dreamaVoiceSampleMaxBytes int64 = 20 * 1024 * 1024

var dreamaVoiceSampleID = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,159}$`)

// Persist only owned resource identities. Inline bytes exist in this execution
// copy only, after task admission and before the installed plugin builds a request.
func hydrateDreamaVoiceSamples(ctx context.Context, userID string, input *Input) error {
	protocol := input.Config.InterfaceType
	switch protocol {
	case "dreama-videos-async", "dreama-extension-async", "dreama-audio-async":
	default:
		return nil
	}
	providers, ok := input.Metadata["providerOptions"].(map[string]any)
	if !ok {
		return nil
	}
	options, ok := providers[protocol].(map[string]any)
	if !ok {
		return nil
	}
	raw, exists := options["character_voices"]
	if !exists {
		return nil
	}
	voices, ok := raw.([]any)
	if !ok {
		return errors.New("人物音色参数格式无效")
	}
	prepared := make([]any, len(voices))
	changed := false
	for i, rawVoice := range voices {
		voice, ok := rawVoice.(map[string]any)
		if !ok {
			return fmt.Errorf("人物音色 %d 参数格式无效", i+1)
		}
		prepared[i] = voice
		rawID, exists := voice["sampleResourceId"]
		if !exists {
			continue
		}
		id, ok := rawID.(string)
		if !ok || !dreamaVoiceSampleID.MatchString(id) {
			return errors.New("音色样本资源标识无效，请重新上传")
		}
		if voice["audio"] != nil || voice["voice_id"] != nil {
			return errors.New("音色样本不能同时指定其他音色来源")
		}
		audio, err := readDreamaVoiceSample(ctx, userID, id)
		if err != nil {
			return fmt.Errorf("人物音色 %d：%w", i+1, err)
		}
		item := maps.Clone(voice)
		delete(item, "sampleResourceId")
		item["audio"] = audio
		prepared[i] = item
		changed = true
	}
	if changed {
		options = maps.Clone(options)
		options["character_voices"] = prepared
		providers = maps.Clone(providers)
		providers[protocol] = options
		metadata := maps.Clone(input.Metadata)
		metadata["providerOptions"] = providers
		input.Metadata = metadata
	}
	return nil
}

func readDreamaVoiceSample(ctx context.Context, userID, id string) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", err
	}
	runtime, ok := RuntimeFromContext(ctx)
	if !ok || runtime.Resources == nil || strings.TrimSpace(userID) == "" {
		return "", errors.New("读取音色样本失败：资源端口未接入")
	}
	info, err := runtime.Resources.Lookup(userID, id)
	if err != nil {
		return "", err
	}
	if err := validateDreamaVoiceResource(info, userID, id); err != nil {
		return "", err
	}
	info, body, err := runtime.Resources.Open(userID, id)
	if err != nil {
		return "", err
	}
	if body == nil {
		return "", errors.New("音色样本为空")
	}
	defer body.Close()
	if err := validateDreamaVoiceResource(info, userID, id); err != nil {
		return "", err
	}
	data, err := io.ReadAll(io.LimitReader(body, dreamaVoiceSampleMaxBytes+1))
	if err != nil {
		return "", err
	}
	if err := ctx.Err(); err != nil {
		return "", err
	}
	if len(data) == 0 || int64(len(data)) > dreamaVoiceSampleMaxBytes {
		return "", errors.New("音色样本为空或超过 20 MiB，请重新上传较短的音频")
	}
	mimeType, _, _ := mime.ParseMediaType(info.MimeType)
	return DataURL(mimeType, data), nil
}

func validateDreamaVoiceResource(info ResourceInfo, userID, id string) error {
	if info.UserID != userID || info.ID != id {
		return errors.New("音色样本不属于当前工作区")
	}
	if info.Status != "ready" {
		return errors.New("音色样本尚未上传完成")
	}
	return ValidateDreamaVoiceSampleMetadata(info.Kind, info.MimeType, info.Size)
}

// ValidateDreamaVoiceSampleMetadata is shared by read-only compilation and execution.
// Execution must additionally authorize the resource and enforce the actual byte limit.
func ValidateDreamaVoiceSampleMetadata(kind, contentType string, size int64) error {
	mimeType, _, err := mime.ParseMediaType(contentType)
	if err != nil || !strings.HasPrefix(mimeType, "audio/") || kind != "audio" {
		return errors.New("音色样本不是音频资源，请重新上传音频文件")
	}
	if size <= 0 || size > dreamaVoiceSampleMaxBytes {
		return errors.New("音色样本为空或超过 20 MiB，请重新上传较短的音频")
	}
	return nil
}
