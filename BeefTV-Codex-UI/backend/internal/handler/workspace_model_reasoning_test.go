package handler

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"infinite-canvas/backend/internal/app"
	"infinite-canvas/backend/internal/workspace"

	"github.com/gin-gonic/gin"
)

type modelReasoningPortFixture struct {
	readChannel string
	update      *workspace.ModelReasoningUpdate
	err         error
}

func (f *modelReasoningPortFixture) ReadModelReasoning(_ context.Context, id string) (*workspace.ModelReasoningSettings, error) {
	f.readChannel = id
	return &workspace.ModelReasoningSettings{ChannelID: id, Revision: 2}, f.err
}
func (f *modelReasoningPortFixture) UpdateModelReasoning(_ context.Context, update workspace.ModelReasoningUpdate) (*workspace.ModelReasoningSettings, error) {
	f.update = &update
	return &workspace.ModelReasoningSettings{ChannelID: update.ChannelID, Revision: 3}, f.err
}

func modelReasoningTestRouter(t *testing.T, port *modelReasoningPortFixture) *gin.Engine {
	t.Helper()
	gin.SetMode(gin.TestMode)
	dir := t.TempDir()
	router := gin.New()
	router.Use(WorkspaceMiddleware(workspace.Context{ID: "local", DataDir: dir}))
	router.Use(RuntimeDependenciesMiddleware(RuntimeDependencies{ModelReasoning: port}))
	RegisterWorkspaceRoutes(router.Group("/api"), app.NewLocal(nil, dir))
	return router
}

func TestWorkspaceModelReasoningReturnsEnvelopeAndForwardsNullReset(t *testing.T) {
	port := &modelReasoningPortFixture{}
	router := modelReasoningTestRouter(t, port)
	read := httptest.NewRecorder()
	router.ServeHTTP(read, httptest.NewRequest(http.MethodGet, "/api/workspace/model-config/reasoning?channelId=local-test", nil))
	if read.Code != 200 || port.readChannel != "local-test" {
		t.Fatalf("GET failed: %d", read.Code)
	}
	var envelope struct {
		Code int                              `json:"code"`
		Data workspace.ModelReasoningSettings `json:"data"`
	}
	if json.Unmarshal(read.Body.Bytes(), &envelope) != nil || envelope.Code != 0 || envelope.Data.Revision != 2 {
		t.Fatal("invalid success envelope")
	}
	write := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPut, "/api/workspace/model-config/reasoning", strings.NewReader(`{"channelId":"local-test","model":"model-a","effort":null,"expectedRevision":2}`))
	request.Header.Set("Content-Type", "application/json")
	router.ServeHTTP(write, request)
	if write.Code != 200 || port.update == nil || port.update.Effort != nil || port.update.ExpectedRevision != 2 {
		t.Fatalf("null reset failed: %d", write.Code)
	}
}

func TestWorkspaceModelReasoningRejectsBrowserURLKeyAndMissingCAS(t *testing.T) {
	for _, body := range []string{
		`{"channelId":"local-test","model":"model-a","effort":"high"}`,
		`{"channelId":"local-test","model":"model-a","expectedRevision":2}`,
		`{"channelId":"local-test","model":"model-a","effort":false,"expectedRevision":2}`,
		`{"channelId":"local-test","model":"model-a","effort":"high","expectedRevision":2,"apiKey":"browser-secret"}`,
		`{"channelId":"local-test","model":"model-a","effort":"high","expectedRevision":2,"baseUrl":"https://example.invalid"}`,
	} {
		port := &modelReasoningPortFixture{}
		router := modelReasoningTestRouter(t, port)
		response := httptest.NewRecorder()
		router.ServeHTTP(response, httptest.NewRequest(http.MethodPut, "/api/workspace/model-config/reasoning", strings.NewReader(body)))
		if response.Code != 400 || port.update != nil || bytes.Contains(response.Body.Bytes(), []byte("browser-secret")) {
			t.Fatalf("invalid write accepted: %d", response.Code)
		}
	}
	port := &modelReasoningPortFixture{}
	router := modelReasoningTestRouter(t, port)
	response := httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/api/workspace/model-config/reasoning?channelId=local-test&baseUrl=https://example.invalid", nil))
	if response.Code != 400 || port.readChannel != "" {
		t.Fatal("GET accepted browser URL")
	}
}

func TestWorkspaceModelReasoningConflictHasMachineReadableReason(t *testing.T) {
	port := &modelReasoningPortFixture{err: &workspace.ModelReasoningError{Status: 409, Reason: "settings_conflict", Message: "Refresh settings"}}
	response := httptest.NewRecorder()
	modelReasoningTestRouter(t, port).ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/api/workspace/model-config/reasoning?channelId=local-test", nil))
	if response.Code != 409 || !strings.Contains(response.Body.String(), `"reason":"settings_conflict"`) {
		t.Fatalf("CAS reason lost: %s", response.Body.String())
	}
}

func TestWorkspaceModelReasoningReadsPersistedProviderCredential(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer persisted-fixture-secret" || r.URL.Path != "/v1/reasoning-settings" {
			t.Error("reasoning proxy did not use the saved provider channel")
		}
		_, _ = w.Write([]byte(`{"models":[{"id":"fixture-model","model":"fixture-model","displayName":"Fixture","defaultReasoningEffort":"high","supportedReasoningEfforts":[{"reasoningEffort":"high","description":"Deep"}]}],"overrides":{},"revision":1}`))
	}))
	defer upstream.Close()
	router, store := newModelConfigTestRouter(t)
	config, _ := json.Marshal(map[string]any{"channels": []any{map[string]any{
		"id": "local-fixture", "enabled": true, "baseUrl": upstream.URL + "/v1", "apiKey": "persisted-fixture-secret", "models": []string{"fixture-model"},
		"modelProfiles": []any{map[string]any{"model": "fixture-model", "capability": "text", "protocol": "chat-completion"}},
	}}})
	if err := store.SaveLocalModelConfig(config); err != nil {
		t.Fatal(err)
	}
	response := httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/api/workspace/model-config/reasoning?channelId=local-fixture", nil))
	if response.Code != http.StatusOK || strings.Contains(response.Body.String(), "persisted-fixture-secret") {
		t.Fatalf("persisted configuration proxy failed: status=%d", response.Code)
	}
}
