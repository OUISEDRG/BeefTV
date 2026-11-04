package modelcatalog

import (
	"infinite-canvas/backend/internal/kernel"
	"math"
	"strings"
)

type AudioNumericCapability struct {
	Supported bool    `json:"supported"`
	Min       float64 `json:"min"`
	Max       float64 `json:"max"`
	Step      float64 `json:"step"`
	Default   float64 `json:"default"`
}
type DeclaredAudioVoiceOption struct {
	Value string `json:"value"`
	Label string `json:"label"`
}
type AudioVoiceCapability struct {
	Supported   bool                       `json:"supported"`
	Values      []DeclaredAudioVoiceOption `json:"values"`
	Default     string                     `json:"default"`
	AllowCustom bool                       `json:"allowCustom,omitempty"`
}
type AudioFormatCapability struct {
	Supported bool     `json:"supported"`
	Values    []string `json:"values"`
	Default   string   `json:"default"`
}
type AudioCapabilityConfig struct {
	Voices       AudioVoiceCapability   `json:"voices"`
	Formats      AudioFormatCapability  `json:"formats"`
	Speed        AudioNumericCapability `json:"speed"`
	Pitch        AudioNumericCapability `json:"pitch"`
	Volume       AudioNumericCapability `json:"volume"`
	Instructions ParameterSupport       `json:"instructions"`
	References   struct {
		PromptMaxChars int `json:"promptMaxChars"`
	} `json:"references"`
}

func validateAudioCapabilityConfig(audio *AudioCapabilityConfig) error {
	if audio == nil {
		return kernel.BadAuthRequest("音频模型能力未加载")
	}
	if audio.References.PromptMaxChars < 0 || audio.References.PromptMaxChars > 1000000 {
		return kernel.BadAuthRequest("音频文本长度限制无效")
	}
	voices := make(map[string]bool)
	for _, voice := range audio.Voices.Values {
		if strings.TrimSpace(voice.Value) == "" || voices[voice.Value] {
			return kernel.BadAuthRequest("音频音色选项无效或重复")
		}
		voices[voice.Value] = true
	}
	if audio.Voices.Default != "" && audio.Voices.Supported && !audio.Voices.AllowCustom && !voices[audio.Voices.Default] {
		return kernel.BadAuthRequest("默认音色必须属于支持值")
	}
	if audio.Formats.Supported && !containsCapabilityString(audio.Formats.Values, audio.Formats.Default) {
		return kernel.BadAuthRequest("默认音频格式必须属于支持值")
	}
	for _, field := range []AudioNumericCapability{audio.Speed, audio.Pitch, audio.Volume} {
		for _, value := range []float64{field.Min, field.Max, field.Step, field.Default} {
			if math.IsNaN(value) || math.IsInf(value, 0) {
				return kernel.BadAuthRequest("音频参数范围必须为有限数值")
			}
		}
		if field.Min > field.Max || field.Step <= 0 || field.Default < field.Min || field.Default > field.Max {
			return kernel.BadAuthRequest("音频参数范围或默认值无效")
		}
	}
	return nil
}
