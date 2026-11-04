package modelcatalog

import (
	"encoding/json"
	"reflect"
	"testing"
)

const declaredAudioJSON = `{"version":1,"audio":{"voices":{"supported":false,"values":[],"default":""},"formats":{"supported":true,"values":["mp3"],"default":"mp3"},"speed":{"supported":true,"min":0.8,"max":1.2,"step":0.1,"default":1},"pitch":{"supported":false,"min":0,"max":0,"step":1,"default":0},"volume":{"supported":false,"min":1,"max":1,"step":1,"default":1},"instructions":{"supported":false},"references":{"promptMaxChars":1000}}}`

func TestDeclaredAudioCapabilitySurvivesNormalization(t *testing.T) {
	capability, err := DecodeModelCapabilityConfig(declaredAudioJSON)
	if err != nil {
		t.Fatal(err)
	}
	normalized, err := NormalizeModelCapabilityConfigForModel("audio", "openai-audio", "unseen", capability)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := json.Marshal(normalized)
	var actual, expected any
	_ = json.Unmarshal(body, &actual)
	_ = json.Unmarshal([]byte(declaredAudioJSON), &expected)
	if string(body) == "null" {
		t.Fatal("explicit audio capability was discarded")
	}
	if !reflect.DeepEqual(actual, expected) {
		t.Fatalf("capability changed: %s", body)
	}
	var fields map[string]json.RawMessage
	_ = json.Unmarshal(body, &fields)
	if len(fields["audio"]) == 0 {
		t.Fatal("audio controls were not retained")
	}
}

func TestResolutionVariantRejectsUnsupportedDurationBeforeSubmission(t *testing.T) {
	profile := DefaultModelCapabilityConfig("openai-videos").Video
	profile.ResolutionVariants = map[string]VideoResolutionVariant{"1080p": {
		Duration:   VideoDurationConfig{Selection: "enum", Values: []int{5}, Default: 5},
		References: map[string]json.RawMessage{"maxImages": json.RawMessage(`1`), "minImageWidth": json.RawMessage(`128`)},
	}}
	input := TaskInput{Mode: "video", Prompt: "scene", Config: TaskConfig{InterfaceType: "openai-videos", Model: "unseen", VQuality: "1080p", Size: "16:9", VideoSeconds: "10"}}
	if err := ValidateVideoTaskParameters(profile, input); err == nil {
		t.Fatal("variant accepted unsupported 10s duration")
	}
	input.Config.VideoSeconds = "5"
	if err := ValidateVideoTaskParameters(profile, input); err != nil {
		t.Fatal(err)
	}
}

func TestAudioCapabilityRejectsInvalidRange(t *testing.T) {
	capability, _ := DecodeModelCapabilityConfig(declaredAudioJSON)
	capability.Audio.Speed.Max = 0.5
	if _, err := NormalizeModelCapabilityConfigForModel("audio", "openai-audio", "unseen", capability); err == nil {
		t.Fatal("invalid speed range accepted")
	}
}

func TestAudioCapabilityAllowsExplicitlyUnselectedVoice(t *testing.T) {
	for _, model := range []string{"dreama-seed-audio-1.0", "dreama-voice-clone"} {
		t.Run(model, func(t *testing.T) {
			capability, _ := DecodeModelCapabilityConfig(declaredAudioJSON)
			capability.Audio.Voices = AudioVoiceCapability{Supported: true, Values: []DeclaredAudioVoiceOption{{Value: "101", Label: "人物音色"}}, Default: ""}
			normalized, err := NormalizeModelCapabilityConfigForModel("audio", "openai-audio", model, capability)
			if err != nil {
				t.Fatal(err)
			}
			if normalized.Audio.Voices.Default != "" {
				t.Fatal("unselected voice became a default selection")
			}
			capability.Audio.Voices.Default = "removed"
			if _, err := NormalizeModelCapabilityConfigForModel("audio", "openai-audio", model, capability); err == nil {
				t.Fatal("unknown nonempty default accepted")
			}
		})
	}
}

func TestVideoOperationSemanticsSurviveNormalization(t *testing.T) {
	capability := DefaultModelCapabilityConfig("openai-videos")
	body, _ := json.Marshal(capability)
	var fields map[string]any
	_ = json.Unmarshal(body, &fields)
	video := fields["video"].(map[string]any)
	video["promptRequired"] = false
	video["durationSemantics"] = "additional"
	video["standaloneOutput"] = true
	video["ratioSource"] = "project"
	body, _ = json.Marshal(fields)
	decoded, err := DecodeModelCapabilityConfig(string(body))
	if err != nil {
		t.Fatal(err)
	}
	normalized, err := NormalizeModelCapabilityConfigForModel("video", "openai-videos", "unseen", decoded)
	if err != nil {
		t.Fatal(err)
	}
	body, _ = json.Marshal(normalized)
	_ = json.Unmarshal(body, &fields)
	result := fields["video"].(map[string]any)
	if result["promptRequired"] != false || result["durationSemantics"] != "additional" || result["standaloneOutput"] != true || result["ratioSource"] != "project" {
		t.Fatalf("operation semantics were discarded: %s", body)
	}
}
