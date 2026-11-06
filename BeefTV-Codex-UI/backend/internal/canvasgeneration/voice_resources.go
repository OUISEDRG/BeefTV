package canvasgeneration

import (
	"regexp"

	"infinite-canvas/backend/internal/generation"
)

var voiceResourceID = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,159}$`)

func validateVoiceResources(item Item, ownedReady func(string) (ResourceMetadata, error)) error {
	protocol, _ := item.Config["interfaceType"].(string)
	if protocol != "dreama-videos-async" && protocol != "dreama-extension-async" && protocol != "dreama-audio-async" {
		return nil
	}
	providers, _ := item.Metadata["providerOptions"].(map[string]any)
	options, _ := providers[protocol].(map[string]any)
	if options["character_voices"] == nil {
		return nil
	}
	voices, ok := options["character_voices"].([]any)
	if !ok {
		return failure("generation_compiler_contract", "画布编译器返回的音色绑定无效")
	}
	for _, raw := range voices {
		voice, ok := raw.(map[string]any)
		if !ok {
			return failure("generation_compiler_contract", "画布编译器返回的音色绑定无效")
		}
		if _, inline := voice["audio"]; inline {
			return failure("generation_voice_not_saved", "音色样本必须保存到当前工作区，不能内嵌媒体")
		}
		value, hasSample := voice["sampleResourceId"]
		if !hasSample {
			continue
		}
		if voice["voice_id"] != nil {
			return failure("generation_voice_invalid", "音色样本不能同时指定其他音色来源")
		}
		id, ok := value.(string)
		if !ok || !voiceResourceID.MatchString(id) {
			return failure("generation_voice_not_saved", "音色样本资源标识无效")
		}
		resource, err := ownedReady(id)
		if err != nil {
			return failure("generation_voice_unavailable", "音色样本未就绪或不属于当前工作区")
		}
		if err := generation.ValidateDreamaVoiceSampleMetadata(resource.Kind, resource.MimeType, resource.Size); err != nil {
			return failure("generation_voice_invalid", err.Error())
		}
	}
	return nil
}
