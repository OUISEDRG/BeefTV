package dreamaintegration

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type transportFunc func(*http.Request) (*http.Response, error)

func (f transportFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func TestEnsureUsesInstalledRuntimeAndNeverBrowserExecutable(t *testing.T) {
	dir := t.TempDir()
	runtime := RuntimeConfig{SchemaVersion: 1, PythonExecutable: filepath.Join(dir, "python.exe"), BridgeDirectory: dir, Origin: "http://127.0.0.1:18765"}
	os.WriteFile(runtime.PythonExecutable, []byte("fixture"), 0600)
	os.WriteFile(filepath.Join(dir, "server.py"), []byte("fixture"), 0600)
	data, _ := json.Marshal(runtime)
	os.WriteFile(filepath.Join(dir, "dreama-integration.json"), data, 0600)
	svc := New(fixture(t, runtime.Origin))
	started := 0
	svc.launch = func(got RuntimeConfig) error {
		started++
		if got.PythonExecutable != runtime.PythonExecutable || got.BridgeDirectory != dir {
			t.Fatal("runtime changed")
		}
		return nil
	}
	svc.client = &http.Client{Transport: transportFunc(func(r *http.Request) (*http.Response, error) {
		if started == 0 {
			return nil, errors.New("not running")
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(`{"schemaVersion":1,"service":"dreama-preflight"}`)), Header: http.Header{}}, nil
	})}
	if _, err := svc.Ensure(context.Background(), "dreama-local-preflight", dir); err != nil || started != 1 {
		t.Fatalf("ensure %v starts %d", err, started)
	}
	if _, err := svc.Ensure(context.Background(), "dreama-local-preflight", dir); err != nil || started != 1 {
		t.Fatal("existing process was not reused")
	}
}
func TestEnsureDoesNotLaunchForOccupiedOrMismatchedService(t *testing.T) {
	svc := New(fixture(t, "http://127.0.0.1:18765"))
	started := false
	svc.launch = func(RuntimeConfig) error { started = true; return nil }
	svc.client = &http.Client{Transport: transportFunc(func(r *http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 404, Body: io.NopCloser(strings.NewReader("not integration")), Header: http.Header{}}, nil
	})}
	if _, err := svc.Ensure(context.Background(), "dreama-local-preflight", t.TempDir()); err == nil || started {
		t.Fatal("occupied service relaunched")
	}
}
