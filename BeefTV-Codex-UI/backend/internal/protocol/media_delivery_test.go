package protocol

import (
	"context"
	"net/http"
	"testing"
)

func TestMediaAckPollCannotChangeOriginalProviderReceipt(t *testing.T) {
	adapter := manifestAdapter{manifest: Manifest{
		Ack:      &ManifestOperation{},
		Response: ManifestResponse{TaskIDPaths: []string{"id"}, StatusPaths: []string{"status"}},
	}}
	ctx := PollContext{TaskID: "original-job"}
	if _, err := adapter.ParsePoll(context.Background(), ctx, []byte(`{"id":"other-job","status":"processing"}`)); err == nil {
		t.Fatal("pending response redirected an ACK-capable task to another receipt")
	}
	result, err := adapter.ParsePoll(context.Background(), ctx, []byte(`{"id":"original-job","status":"processing"}`))
	if err != nil || result.TaskID != ctx.TaskID || result.Status != StatusProcessing {
		t.Fatalf("original pending receipt: %+v %v", result, err)
	}
}

func TestMediaAckManifestRequiresTaskScopedReadOperations(t *testing.T) {
	base := ManifestProvider{ID: "media-fixture", Label: "Media Fixture",
		Capabilities: []Capability{CapabilityImage}, Scopes: []Surface{SurfaceCanvas},
		Create:   ManifestOperation{Method: http.MethodPost, Path: "/jobs"},
		Ack:      &ManifestOperation{Method: http.MethodPost, Path: "/jobs/{{taskId}}/ack"},
		Response: ManifestResponse{Artifacts: map[string]any{"$ref": "response.artifacts"}}}
	for _, tc := range []struct {
		name              string
		ack, poll, result *ManifestOperation
		wantError         bool
	}{
		{"read", base.Ack, &ManifestOperation{Method: http.MethodGet, Path: "/jobs/{{taskId}}"}, nil, false},
		{"write poll", base.Ack, &ManifestOperation{Method: http.MethodPost, Path: "/jobs/{{taskId}}"}, nil, true},
		{"write result", base.Ack, &ManifestOperation{Method: http.MethodGet, Path: "/jobs/{{taskId}}"}, &ManifestOperation{Method: http.MethodDelete, Path: "/jobs/{{taskId}}/result"}, true},
		{"unscoped poll", base.Ack, &ManifestOperation{Method: http.MethodGet, Path: "/jobs/latest"}, nil, true},
		{"ACK template override", &ManifestOperation{Method: http.MethodPost, Path: "/jobs/{{taskId}}/ack", PathTemplate: "/jobs/global/ack"}, &ManifestOperation{Method: http.MethodGet, Path: "/jobs/{{taskId}}"}, nil, true},
		{"poll template override", base.Ack, &ManifestOperation{Method: http.MethodGet, Path: "/jobs/{{taskId}}", PathTemplate: "/jobs/global"}, nil, true},
		{"result template override", base.Ack, &ManifestOperation{Method: http.MethodGet, Path: "/jobs/{{taskId}}"}, &ManifestOperation{Method: http.MethodGet, Path: "/jobs/{{taskId}}/result", PathTemplate: "/jobs/global/result"}, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			provider := base
			provider.Ack, provider.Poll, provider.Result = tc.ack, tc.poll, tc.result
			manifest := Manifest{APIVersion: "beeftv.plugin/v3",
				Metadata:    Metadata{ID: "media-fixture", Name: "Media Fixture", Version: "1.0.0"},
				Contributes: ManifestContributions{Providers: []ManifestProvider{provider}}}
			if err := ValidateManifest(manifest); (err != nil) != tc.wantError {
				t.Fatalf("want error %v, got %v", tc.wantError, err)
			}
		})
	}
}

func TestMediaAckManifestRequiresOriginalReceiptInURLPath(t *testing.T) {
	for _, unscoped := range []string{
		"/jobs/latest#{{taskId}}",
		"/jobs/latest?receipt={{taskId}}",
		"/jobs/{{taskId}}/../latest",
		"/jobs/{{taskId}}/%2e%2e/latest",
	} {
		t.Run(unscoped, func(t *testing.T) {
			for _, field := range []string{"ack", "poll", "result"} {
				t.Run(field, func(t *testing.T) {
					ack := &ManifestOperation{Method: http.MethodPost, Path: "/jobs/{{taskId}}/ack"}
					poll := &ManifestOperation{Method: http.MethodGet, Path: "/jobs/{{taskId}}?state=1"}
					result := &ManifestOperation{Method: http.MethodGet, Path: "/jobs/{{taskId}}/result"}
					operations := map[string]*ManifestOperation{"ack": ack, "poll": poll, "result": result}
					operations[field].Path = unscoped
					provider := ManifestProvider{ID: "media-fixture", Label: "Media Fixture",
						Capabilities: []Capability{CapabilityImage}, Scopes: []Surface{SurfaceCanvas},
						Create: ManifestOperation{Method: http.MethodPost, Path: "/jobs"},
						Ack:    ack, Poll: poll, Result: result,
						Response: ManifestResponse{Artifacts: map[string]any{"$ref": "response.artifacts"}}}
					manifest := Manifest{APIVersion: "beeftv.plugin/v3",
						Metadata:    Metadata{ID: "media-fixture", Name: "Media Fixture", Version: "1.0.0"},
						Contributes: ManifestContributions{Providers: []ManifestProvider{provider}}}
					if err := ValidateManifest(manifest); err == nil {
						t.Fatalf("accepted unscoped %s path %q", field, unscoped)
					}
				})
			}
		})
	}
}
