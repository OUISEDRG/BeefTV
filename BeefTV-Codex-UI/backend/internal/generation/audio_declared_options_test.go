package generation

import (
	"encoding/json"
	"testing"
)

func TestDeclaredAudioNumbersReachPluginRequest(t *testing.T) {
	var input Input
	if err := json.Unmarshal([]byte(`{"mode":"audio","config":{"audioPitch":1.5,"audioVolume":1.2}}`), &input); err != nil {
		t.Fatal(err)
	}
	request := ProtocolRequestFromInput(input)
	if request.Extra["audioPitch"] != "1.5" || request.Extra["audioVolume"] != "1.2" {
		t.Fatalf("declared options lost: %#v", request.Extra)
	}
}
