package handler

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
	"infinite-canvas/backend/internal/app"
	"infinite-canvas/backend/internal/workspace"
)

type dreamaConfigFixture []byte

func TestDreamaFacadeBusinessDiscoveryIsAllowlisted(t *testing.T) {
	catalog := NewBusinessCatalog(gin.RoutesInfo{
		{Method: "GET", Path: "/api/integrations/dreama/v1/status"},
		{Method: "POST", Path: "/api/integrations/dreama/v1/ensure"},
		{Method: "POST", Path: "/api/integrations/dreama/v1/catalog/refresh"},
		{Method: "POST", Path: "/api/integrations/dreama/v1/tasks/:id/cancel"},
		{Method: "POST", Path: "/api/integrations/dreama/v1/submit"},
		{Method: "GET", Path: "/api/integrations/other/secrets"},
	})
	if len(catalog.Tools) != 4 {
		t.Fatalf("wrong discovery: %#v", catalog.Tools)
	}
	for _, tool := range catalog.Tools {
		if tool.ReadOnly != (tool.Method == "GET") || tool.SubmitsGeneration {
			t.Fatalf("incorrect API policy: %#v", tool)
		}
	}
}

func (f dreamaConfigFixture) ReadLocalModelConfig() ([]byte, error) { return f, nil }
func (f dreamaConfigFixture) SaveLocalModelConfig([]byte) error     { return nil }
func TestDreamaFacadeEnvelopeAndStrictRequests(t *testing.T) {
	gin.SetMode(gin.TestMode)
	calls := 0
	bridge := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Header.Get("Authorization") != "Bearer saved-secret" {
			t.Error("credential not from saved channel")
		}
		w.Write([]byte(`{"items":[]}`))
	}))
	defer bridge.Close()
	config, _ := json.Marshal(map[string]any{"channels": []any{map[string]any{"id": "dreama", "enabled": true, "baseUrl": bridge.URL + "/v1", "apiKey": "saved-secret", "modelProfiles": []any{map[string]any{"protocol": "dreama-videos-async"}}}}})
	dir := t.TempDir()
	router := gin.New()
	router.Use(WorkspaceMiddleware(workspace.Context{ID: "local", DataDir: dir}))
	router.Use(RuntimeDependenciesMiddleware(RuntimeDependencies{ProviderConfig: dreamaConfigFixture(config)}))
	RegisterWorkspaceRoutes(router.Group("/api"), app.NewLocal(nil, dir))
	good := httptest.NewRecorder()
	router.ServeHTTP(good, httptest.NewRequest("GET", "/api/integrations/dreama/v1/projects?channelId=dreama", nil))
	if good.Code != 200 || calls != 1 || !strings.Contains(good.Body.String(), `"items":[]`) {
		t.Fatalf("bad facade response %d %s", good.Code, good.Body.String())
	}
	for _, path := range []string{"?channelId=dreama&channelId=x", "?channelId=dreama&baseUrl=http://evil.invalid", "?channelId=dreama&apiKey=bad", ""} {
		bad := httptest.NewRecorder()
		router.ServeHTTP(bad, httptest.NewRequest("GET", "/api/integrations/dreama/v1/projects"+path, nil))
		if bad.Code != 400 || calls != 1 {
			t.Fatal("invalid query reached bridge")
		}
	}
	bad := httptest.NewRecorder()
	router.ServeHTTP(bad, httptest.NewRequest("POST", "/api/integrations/dreama/v1/ensure", strings.NewReader(`{"channelId":"dreama","executable":"evil.exe"}`)))
	if bad.Code != 400 || calls != 1 {
		t.Fatal("browser executable accepted")
	}
}
