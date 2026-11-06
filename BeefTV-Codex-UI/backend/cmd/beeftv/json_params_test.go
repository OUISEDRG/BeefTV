package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type forbiddenParamsReader struct{}

func (forbiddenParamsReader) Read([]byte) (int, error) {
	panic("inline parameters must not read stdin")
}

func TestJSONParamsStdinPreservesLongUnicodeDocument(t *testing.T) {
	want := `{"prompt":"` + strings.Repeat("镜头正文", 10000) + `\n原文  空格"}`
	got, err := readJSONParams("-", strings.NewReader(want))
	if err != nil || string(got) != want {
		t.Fatalf("stdin body changed: %v", err)
	}
	if _, err := readJSONParams(`{"inline":true}`, forbiddenParamsReader{}); err != nil {
		t.Fatal(err)
	}
}
func TestJSONParamsRejectMalformedAndOversizeWithoutEcho(t *testing.T) {
	for _, body := range []string{"", "PRIVATE_BAD_JSON", `{} {}`, strings.Repeat(" ", maximumJSONParams+1)} {
		_, err := readJSONParams("-", strings.NewReader(body))
		if err == nil {
			t.Fatal("accepted invalid params")
		}
		if strings.Contains(err.Error(), "PRIVATE") {
			t.Fatal("echoed body")
		}
	}
}

func TestJSONParamsCommandsReadStdin(t *testing.T) {
	want := strings.Repeat("剧本正文", 10000) + "\n保留  空格"
	for _, command := range []string{"ops", "business"} {
		t.Run(command, func(t *testing.T) {
			var received map[string]any
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				if r.URL.Path == "/business/tools" {
					_, _ = w.Write([]byte(`{"code":0,"data":{"tools":[{"id":"fixture","method":"POST","path":"/fixture"}]}}`))
					return
				}
				if err := json.NewDecoder(r.Body).Decode(&received); err != nil {
					t.Error(err)
				}
				_, _ = w.Write([]byte(`{"code":0,"data":{"ok":true}}`))
			}))
			defer server.Close()
			c := &client{baseURL: server.URL, fixedBaseURL: true, http: server.Client()}
			payload := map[string]any{"prompt": want}
			if command == "business" {
				payload = map[string]any{"body": payload}
			}
			encoded, _ := json.Marshal(payload)
			path := filepath.Join(t.TempDir(), "params.json")
			if err := os.WriteFile(path, encoded, 0600); err != nil {
				t.Fatal(err)
			}
			input, err := os.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			defer input.Close()
			original := os.Stdin
			os.Stdin = input
			defer func() { os.Stdin = original }()
			if command == "ops" {
				err = runOpsCall(c, []string{"canvas.generation.prepare", "--params", "-"})
			} else {
				err = runBusiness(c, []string{"call", "--tool", "fixture", "--params", "-"})
			}
			if err != nil {
				t.Fatal(err)
			}
			if command == "ops" {
				received = received["params"].(map[string]any)
			}
			if received["prompt"] != want {
				t.Fatal("command changed stdin prompt")
			}
		})
	}
}
