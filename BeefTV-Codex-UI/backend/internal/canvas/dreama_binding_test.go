package canvas

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

func TestDreamaBindingSurvivesHistoryRestore(t *testing.T) {
	for _, oldBinding := range []string{``, `,"dreama":{"schemaVersion":1,"projectId":12,"promptMode":"drama","aspectRatio":"16:9","defaultSeriesId":31,"revision":1}`} {
		svc := newCanvasHistoryTestService(t)
		old := fmt.Sprintf(`{"id":"history","revision":0,"title":"original","nodes":[{"id":"original-node"}],"connections":[],"integrations":{"other":{"value":"old"}%s}}`, oldBinding)
		if _, err := svc.UpsertUserCanvasProject("owner", json.RawMessage(old)); err != nil {
			t.Fatal(err)
		}
		latest := `{"id":"history","revision":1,"title":"edited","nodes":[],"connections":[],"integrations":{"other":{"value":"new"},"dreama":{"schemaVersion":1,"projectId":12,"promptMode":"drama","aspectRatio":"16:9","defaultSeriesId":32,"revision":2}}}`
		if _, err := svc.UpsertUserCanvasProject("owner", json.RawMessage(latest)); err != nil {
			t.Fatal(err)
		}
		history, err := svc.CanvasHistory("owner", "history")
		if err != nil || len(history.Snapshots) != 1 {
			t.Fatalf("history: %#v %v", history, err)
		}
		revision := int64(2)
		if _, err := svc.RestoreCanvasHistory("owner", "history", history.Snapshots[0].ID, &revision); err != nil {
			t.Fatal(err)
		}
		raw, err := svc.UserCanvasProject("owner", "history")
		if err != nil {
			t.Fatal(err)
		}
		var restored struct {
			Title        string `json:"title"`
			Integrations struct {
				Dreama struct {
					Revision int `json:"revision"`
					Series   int `json:"defaultSeriesId"`
				} `json:"dreama"`
				Other struct {
					Value string `json:"value"`
				} `json:"other"`
			} `json:"integrations"`
		}
		if err := json.Unmarshal(raw, &restored); err != nil {
			t.Fatal(err)
		}
		if restored.Title != "original" || restored.Integrations.Dreama.Revision != 2 || restored.Integrations.Dreama.Series != 32 || restored.Integrations.Other.Value != "old" {
			t.Fatalf("incorrect history restore: %s", raw)
		}
	}
}

func TestDreamaBindingRoundtripAndProjectImmutability(t *testing.T) {
	svc := newCanvasHistoryTestService(t)
	raw := json.RawMessage(`{"id":"dreama-canvas","revision":0,"title":"test","nodes":[],"connections":[],"integrations":{"other":{"keep":true},"dreama":{"schemaVersion":1,"projectId":12,"projectName":"test","promptMode":"drama","aspectRatio":"16:9","defaultSeriesId":31,"revision":1}}}`)
	saved, err := svc.UpsertUserCanvasProject("owner", raw)
	if err != nil {
		t.Fatal(err)
	}
	_, after, err := svc.CommitUserCanvasDocument("owner", "dreama-canvas", saved.Revision, json.RawMessage(`{"title":"new"}`))
	if err != nil || !strings.Contains(string(after), `"projectId":12`) || !strings.Contains(string(after), `"keep":true`) {
		t.Fatalf("binding lost %s %v", after, err)
	}
	current, err := svc.UserCanvasProject("owner", "dreama-canvas")
	if err != nil {
		t.Fatal(err)
	}
	var document map[string]any
	json.Unmarshal(current, &document)
	integrations := document["integrations"].(map[string]any)
	binding := integrations["dreama"].(map[string]any)
	binding["projectId"] = float64(99)
	binding["revision"] = float64(2)
	wrong, _ := json.Marshal(document)
	if _, err = svc.UpsertUserCanvasProject("owner", wrong); err == nil {
		t.Fatal("existing project rebound")
	}
	binding["projectId"] = float64(12)
	binding["defaultSeriesId"] = float64(32)
	valid, _ := json.Marshal(document)
	if _, err = svc.UpsertUserCanvasProject("owner", valid); err != nil {
		t.Fatal(err)
	}
	current, _ = svc.UserCanvasProject("owner", "dreama-canvas")
	json.Unmarshal(current, &document)
	delete(document, "integrations")
	removed, _ := json.Marshal(document)
	if _, err = svc.UpsertUserCanvasProject("owner", removed); err == nil {
		t.Fatal("binding silently cleared")
	}
	document["id"] = "copied-canvas"
	document["revision"] = float64(0)
	copied, _ := json.Marshal(document)
	if _, err = svc.UpsertUserCanvasProject("owner", copied); err != nil {
		t.Fatalf("unbound copy rejected: %v", err)
	}
}
