package canvasgeneration

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
)

func fixtureRequest() Request {
	return Request{SchemaVersion: 1, Canvas: json.RawMessage(`{"id":"canvas","revision":7}`), Config: json.RawMessage(`{"apiKey":"PRIVATE"}`), NodeIDs: []string{"shot"}, Assets: []any{}}
}

func fixtureOutput() Compiled {
	return Compiled{SchemaVersion: 1, Items: []Item{{NodeID: "shot", Kind: "video", ModelKey: "dreama::model", Prompt: "正文 @图片1", Config: map[string]any{"interfaceType": "dreama-videos-async"}, ReferenceImages: []Reference{{ID: "source", StorageKey: "resource:owned"}}, Preview: json.RawMessage(`{"prompt":"正文 @图片1","media":[{"id":"source","index":1}]}`)}}}
}

func TestPrepareIsReadOnlyAndReturnsStablePublicSnapshot(t *testing.T) {
	calls := 0
	runner := func(_ context.Context, request Request) (Compiled, error) {
		calls++
		if request.Config == nil {
			t.Fatal("missing config")
		}
		return fixtureOutput(), nil
	}
	owned := func(id string) (ResourceMetadata, error) {
		if id != "owned" {
			t.Fatal(id)
		}
		return ResourceMetadata{}, nil
	}
	first, err := Prepare(context.Background(), fixtureRequest(), 12, runner, owned)
	if err != nil {
		t.Fatal(err)
	}
	second, err := Prepare(context.Background(), fixtureRequest(), 12, runner, owned)
	if err != nil || first.SnapshotHash != second.SnapshotHash || calls != 2 {
		t.Fatalf("unstable: %+v %v", second, err)
	}
	if first.GenerationSubmitted || first.SubmissionAvailable || first.CanvasRevision != 7 || first.ModelConfigRevision != 12 || len(first.Items) != 1 {
		t.Fatalf("bad summary: %+v", first)
	}
	encoded, _ := json.Marshal(first)
	var public map[string]any
	_ = json.Unmarshal(encoded, &public)
	if _, ok := public["config"]; ok {
		t.Fatal("configuration escaped")
	}
	item := public["items"].([]any)[0].(map[string]any)
	if _, ok := item["config"]; ok {
		t.Fatal("compiled configuration escaped")
	}
	if _, ok := item["metadata"]; ok {
		t.Fatal("private metadata escaped")
	}
}

func TestPrepareRejectsForeignMissingAndUnpersistedReferences(t *testing.T) {
	for _, key := range []string{"resource:foreign", "https://untrusted/image", ""} {
		t.Run(key, func(t *testing.T) {
			output := fixtureOutput()
			output.Items[0].ReferenceImages[0].StorageKey = key
			_, err := Prepare(context.Background(), fixtureRequest(), 12, func(context.Context, Request) (Compiled, error) { return output, nil }, func(string) (ResourceMetadata, error) { return ResourceMetadata{}, errors.New("not owned") })
			if err == nil {
				t.Fatal("accepted unavailable resource")
			}
		})
	}
}

func TestPrepareRejectsCompilerWrongIdentityAndChangesHashWithInput(t *testing.T) {
	output := fixtureOutput()
	output.Items[0].NodeID = "other"
	if _, err := Prepare(context.Background(), fixtureRequest(), 12, func(context.Context, Request) (Compiled, error) { return output, nil }, func(string) (ResourceMetadata, error) { return ResourceMetadata{}, nil }); err == nil {
		t.Fatal("accepted wrong node")
	}
	output = fixtureOutput()
	run := func(context.Context, Request) (Compiled, error) { return output, nil }
	first, err := Prepare(context.Background(), fixtureRequest(), 12, run, func(string) (ResourceMetadata, error) { return ResourceMetadata{}, nil })
	if err != nil {
		t.Fatal(err)
	}
	output.Items[0].Prompt = "changed"
	second, err := Prepare(context.Background(), fixtureRequest(), 12, run, func(string) (ResourceMetadata, error) { return ResourceMetadata{}, nil })
	if err != nil || first.SnapshotHash == second.SnapshotHash {
		t.Fatal("changed snapshot retained identity")
	}
}

func TestPrepareRejectsUnavailableVoiceResources(t *testing.T) {
	output := fixtureOutput()
	output.Items[0].Metadata = map[string]any{"providerOptions": map[string]any{"dreama-videos-async": map[string]any{
		"character_voices": []any{map[string]any{"image_index": float64(1), "name": "person", "sampleResourceId": "foreign-audio"}},
	}}}
	_, err := Prepare(context.Background(), fixtureRequest(), 12, func(context.Context, Request) (Compiled, error) { return output, nil }, func(id string) (ResourceMetadata, error) {
		if id == "foreign-audio" {
			return ResourceMetadata{}, errors.New("not owned")
		}
		return ResourceMetadata{}, nil
	})
	if err == nil {
		t.Fatal("accepted an unavailable voice sample")
	}
}

func TestPrepareValidatesVoiceMetadataAndPreservesResourceIdentity(t *testing.T) {
	for _, tc := range []struct {
		name     string
		resource ResourceMetadata
		fail     bool
	}{
		{"ready audio", ResourceMetadata{Kind: "audio", MimeType: "audio/mpeg", Size: 12}, false},
		{"not audio", ResourceMetadata{Kind: "image", MimeType: "image/png", Size: 12}, true},
		{"empty audio", ResourceMetadata{Kind: "audio", MimeType: "audio/mpeg", Size: 0}, true},
		{"oversized audio", ResourceMetadata{Kind: "audio", MimeType: "audio/mpeg", Size: 20*1024*1024 + 1}, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			output := fixtureOutput()
			output.Items[0].Metadata = map[string]any{"providerOptions": map[string]any{"dreama-videos-async": map[string]any{
				"character_voices": []any{map[string]any{"image_index": float64(1), "name": "person", "sampleResourceId": "sample"}},
			}}}
			before, _ := json.Marshal(output)
			prepared, err := Prepare(context.Background(), fixtureRequest(), 12, func(context.Context, Request) (Compiled, error) { return output, nil }, func(id string) (ResourceMetadata, error) {
				if id == "owned" {
					return ResourceMetadata{}, nil
				}
				if id == "sample" {
					return tc.resource, nil
				}
				return ResourceMetadata{}, errors.New("missing")
			})
			if (err != nil) != tc.fail {
				t.Fatalf("error=%v; want failure %v", err, tc.fail)
			}
			if !tc.fail {
				after, _ := json.Marshal(prepared.Compiled)
				if string(before) != string(after) {
					t.Fatal("preparation changed stored voice identity")
				}
				if prepared.GenerationSubmitted {
					t.Fatal("preparation submitted a task")
				}
			}
		})
	}
}

func TestPrepareRejectsEmbeddedAndMalformedVoiceSamples(t *testing.T) {
	for _, voice := range []map[string]any{
		{"image_index": float64(1), "audio": "data:audio/mpeg;base64,YQ=="},
		{"image_index": float64(1), "sampleResourceId": "../private/audio.mp3"},
		{"image_index": float64(1), "sampleResourceId": "sample", "voice_id": "catalog"},
	} {
		output := fixtureOutput()
		output.Items[0].Metadata = map[string]any{"providerOptions": map[string]any{"dreama-videos-async": map[string]any{"character_voices": []any{voice}}}}
		_, err := Prepare(context.Background(), fixtureRequest(), 12, func(context.Context, Request) (Compiled, error) { return output, nil }, func(string) (ResourceMetadata, error) {
			return ResourceMetadata{Kind: "audio", MimeType: "audio/mpeg", Size: 12}, nil
		})
		if err == nil {
			t.Fatal("accepted embedded, malformed or ambiguous voice sample")
		}
	}
}
