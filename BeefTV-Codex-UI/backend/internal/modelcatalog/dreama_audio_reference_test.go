package modelcatalog

import (
	"strings"
	"testing"
)

// This is the ordinary-audio projection of the upstream 九梦 2.5 Pro limits.
// Voice bindings use providerOptions and must not consume this array's capacity.
func TestDreamaOrdinaryAudioCatalogLimits(t *testing.T) {
	profile := &VideoCapabilityConfig{Operations: []string{"image_to_video", "reference_to_video", "audio_to_video"},
		References: VideoReferenceConfig{MaxImages: 30, MaxAudios: 10, MaxAudioBytes: 15 * 1024 * 1024, MinAudioDuration: 2, MaxAudioDuration: 30, MaxAudioTotalDuration: 30},
	}
	version := "ordinary-audio-v1"
	base := TaskInput{Mode: "video", Config: TaskConfig{InterfaceType: "dreama-videos-async", Model: "catalog-model", VideoCapabilitiesVersion: &version}, Metadata: map[string]any{"videoEditOperation": "audio_to_video"}}
	for _, tc := range []struct {
		name     string
		count    int
		duration int64
		bytes    int64
		image    bool
		want     string
	}{
		{name: "pure audio", count: 1, duration: 2000, bytes: 1000},
		{name: "image and audio", count: 2, duration: 2000, bytes: 1000, image: true},
		{name: "catalog count boundary", count: 10, duration: 2000, bytes: 1000},
		{name: "count overflow", count: 11, duration: 2000, bytes: 1000, want: "10"},
		{name: "minimum duration", count: 1, duration: 1999, bytes: 1000, want: "2–30 秒"},
		{name: "maximum duration", count: 1, duration: 30001, bytes: 1000, want: "2–30 秒"},
		{name: "total boundary", count: 2, duration: 15000, bytes: 1000},
		{name: "total overflow", count: 2, duration: 15001, bytes: 1000, want: "总时长"},
		{name: "file boundary", count: 1, duration: 2000, bytes: 15 * 1024 * 1024},
		{name: "file overflow", count: 1, duration: 2000, bytes: 15*1024*1024 + 1, want: "文件过大"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			input := base
			if tc.image {
				input.ReferenceImages = []MediaRef{{Bytes: 1000, Width: 720, Height: 720}}
				input.Metadata = map[string]any{"videoEditOperation": "image_to_video"}
			}
			for i := 0; i < tc.count; i++ {
				input.ReferenceAudios = append(input.ReferenceAudios, MediaRef{Bytes: tc.bytes, DurationMs: tc.duration})
			}
			err := ValidateVideoReferenceMedia(profile, input)
			if tc.want == "" && err != nil || tc.want != "" && (err == nil || !strings.Contains(err.Error(), tc.want)) {
				t.Fatalf("expected %q, got %v", tc.want, err)
			}
			if len(input.ReferenceAudios) != tc.count {
				t.Fatal("validation must reject, never prune references")
			}
		})
	}
}

func TestDreamaOrdinaryVideoCatalogLimits(t *testing.T) {
	profile := &VideoCapabilityConfig{Operations: []string{"reference_to_video", "audio_to_video"},
		References: VideoReferenceConfig{MaxImages: 30, MaxVideos: 10, MaxVideoBytes: 200 * 1024 * 1024, MinVideoDuration: 2, MaxVideoDuration: 30, MaxVideoTotalDuration: 30, MaxAudios: 10, MaxAudioBytes: 15 * 1024 * 1024, MinAudioDuration: 2, MaxAudioDuration: 30, MaxAudioTotalDuration: 30},
	}
	version := "ordinary-multimodal-v1"
	for _, tc := range []struct {
		name     string
		count    int
		duration int64
		bytes    int64
		want     string
	}{
		{name: "three video references plus audio", count: 3, duration: 2000, bytes: 1000},
		{name: "count boundary", count: 10, duration: 2000, bytes: 1000},
		{name: "count overflow", count: 11, duration: 2000, bytes: 1000, want: "10"},
		{name: "minimum duration", count: 1, duration: 1999, bytes: 1000, want: "2–30 秒"},
		{name: "maximum duration", count: 1, duration: 30001, bytes: 1000, want: "2–30 秒"},
		{name: "total boundary independent of audio total", count: 2, duration: 15000, bytes: 1000},
		{name: "total overflow", count: 2, duration: 15001, bytes: 1000, want: "总时长"},
		{name: "file boundary", count: 1, duration: 2000, bytes: 200 * 1024 * 1024},
		{name: "file overflow", count: 1, duration: 2000, bytes: 200*1024*1024 + 1, want: "文件过大"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			input := TaskInput{Mode: "video", Config: TaskConfig{InterfaceType: "dreama-videos-async", Model: "catalog-model", VideoCapabilitiesVersion: &version}, Metadata: map[string]any{"videoEditOperation": "reference_to_video"}, ReferenceAudios: []MediaRef{{Bytes: 1000, DurationMs: 30000}}}
			for i := 0; i < tc.count; i++ {
				input.ReferenceVideos = append(input.ReferenceVideos, MediaRef{Bytes: tc.bytes, DurationMs: tc.duration, Width: 1280, Height: 720})
			}
			err := ValidateVideoReferenceMedia(profile, input)
			if tc.want == "" && err != nil || tc.want != "" && (err == nil || !strings.Contains(err.Error(), tc.want)) {
				t.Fatalf("expected %q, got %v", tc.want, err)
			}
			if len(input.ReferenceVideos) != tc.count || len(input.ReferenceAudios) != 1 {
				t.Fatal("validation must reject, never prune references")
			}
		})
	}
}

func TestDreamaMultivideoDoesNotBroadenSingleSourceOperations(t *testing.T) {
	one, ten := 1, 10
	profile := &VideoCapabilityConfig{Operations: []string{"reference_to_video", "extend"}, References: VideoReferenceConfig{MaxVideos: 10}, OperationReferences: map[string]VideoOperationReferences{
		"reference_to_video": {MaxVideos: &ten},
		"extend":             {MaxVideos: &one},
	}}
	input := TaskInput{Mode: "video", Config: TaskConfig{InterfaceType: "dreama-videos-async"}, ReferenceVideos: []MediaRef{{}, {}}, Metadata: map[string]any{"videoEditOperation": "reference_to_video"}}
	if err := ValidateVideoReferenceMedia(profile, input); err != nil {
		t.Fatal(err)
	}
	input.Metadata = map[string]any{"videoEditOperation": "extend"}
	if err := ValidateVideoReferenceMedia(profile, input); err == nil || !strings.Contains(err.Error(), "1 个参考视频") {
		t.Fatalf("single-source extension should reject two videos: %v", err)
	}
	if len(input.ReferenceVideos) != 2 {
		t.Fatal("extension must reject extra sources, never silently choose one")
	}
}
