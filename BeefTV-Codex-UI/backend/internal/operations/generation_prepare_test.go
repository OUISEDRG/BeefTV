package operations

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
)

type generationPrepareProbe struct {
	Domain
	called int
	denied bool
}

func (p *generationPrepareProbe) UserCanvasProject(userID, id string) (json.RawMessage, error) {
	if p.denied {
		return nil, errors.New("denied")
	}
	return json.RawMessage(`{"id":"canvas","revision":7}`), nil
}
func (p *generationPrepareProbe) PrepareCanvasGeneration(ctx context.Context, user string, doc json.RawMessage, ids []string, revision *int64) (any, error) {
	p.called++
	return map[string]any{"generationSubmitted": false, "nodeIds": ids}, nil
}

func TestGenerationPrepareReadsWithoutTurnOrOperationID(t *testing.T) {
	p := &generationPrepareProbe{}
	result, err := opGenerationPrepare(&Context{UserID: "owner", Domain: p, Caller: ExternalCaller(true)}, json.RawMessage(`{"canvasId":"canvas","nodeIds":["shot"],"expectedRevision":7}`))
	if err != nil || p.called != 1 || result.(map[string]any)["generationSubmitted"] != false {
		t.Fatalf("%#v %v", result, err)
	}
	r := NewRegistry(nil, nil)
	registerGenerationPrepare(r)
	d, ok := r.Descriptor("canvas.generation.prepare")
	if !ok || !d.ReadOnly || d.Replay != "safe" {
		t.Fatal(d)
	}
}

func TestGenerationPrepareRejectsBeforeCompiling(t *testing.T) {
	for _, raw := range []string{`{"canvasId":"canvas","nodeIds":["shot"],"expectedRevision":6}`, `{"canvasId":"canvas","nodeIds":["shot","shot"]}`, `{"canvasId":"canvas","nodeIds":[]}`, `{"canvasId":"canvas","nodeIds":["shot"],"expectedModelConfigRevision":-1}`} {
		p := &generationPrepareProbe{}
		if _, err := opGenerationPrepare(&Context{UserID: "owner", Domain: p}, json.RawMessage(raw)); err == nil || p.called != 0 {
			t.Fatalf("accepted %s", raw)
		}
	}
	p := &generationPrepareProbe{denied: true}
	if _, err := opGenerationPrepare(&Context{UserID: "owner", Domain: p}, json.RawMessage(`{"canvasId":"canvas","nodeIds":["shot"]}`)); err == nil || p.called != 0 {
		t.Fatal("compiled inaccessible canvas")
	}
}
