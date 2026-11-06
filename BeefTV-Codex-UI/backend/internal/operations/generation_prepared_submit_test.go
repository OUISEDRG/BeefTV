package operations

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

type preparedSubmitProbe struct {
	generationPrepareProbe
	submitted int
}

func (p *preparedSubmitProbe) SubmitPreparedCanvasGeneration(_ context.Context, user, operation string, doc json.RawMessage, ids []string, kind string, revision int64, hash string) (any, error) {
	p.submitted++
	return map[string]any{"hash": hash, "revision": revision}, nil
}

func TestPreparedSubmitUsesCanonicalCompilerPort(t *testing.T) {
	p := &preparedSubmitProbe{}
	raw := json.RawMessage(`{"canvasId":"canvas","nodeIds":["shot"],"kind":"video","expectedRevision":7,"expectedModelConfigRevision":12,"expectedSnapshotHash":"` + strings.Repeat("a", 64) + `"}`)
	_, err := opGenerationSubmit(&Context{UserID: "owner", OperationID: "approved-once", Caller: ExternalCaller(false), Domain: p}, raw)
	if err != nil || p.submitted != 1 {
		t.Fatalf("prepared submission did not reach canonical compiler: %v", err)
	}
}

func TestPreparedSubmitRejectsStaleReadOnlyAndMissingIdentity(t *testing.T) {
	for _, tc := range []struct {
		name      string
		revision  int
		caller    Caller
		operation string
		ids       string
	}{
		{"stale", 6, ExternalCaller(false), "op", `["shot"]`},
		{"read only", 7, ExternalCaller(true), "op", `["shot"]`},
		{"missing operation", 7, ExternalCaller(false), "", `["shot"]`},
		{"duplicate nodes", 7, ExternalCaller(false), "op", `["shot","shot"]`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			p := &preparedSubmitProbe{}
			raw, _ := json.Marshal(map[string]any{"canvasId": "canvas", "nodeIds": json.RawMessage(tc.ids), "expectedRevision": tc.revision, "expectedModelConfigRevision": 12, "expectedSnapshotHash": strings.Repeat("a", 64)})
			_, err := opGenerationSubmit(&Context{UserID: "owner", OperationID: tc.operation, Caller: tc.caller, Domain: p}, raw)
			if err == nil || p.submitted != 0 {
				t.Fatal("unsafe prepared submission admitted", err)
			}
		})
	}
}
