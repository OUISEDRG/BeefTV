package handler

import (
	"context"
	"github.com/gin-gonic/gin"
	"infinite-canvas/backend/internal/app"
	"infinite-canvas/backend/internal/workspace"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

type voicePortFixture struct{ calls int }

func (f *voicePortFixture) ReadModelVoices(_ context.Context, id string) (*workspace.ModelVoiceCatalog, error) {
	f.calls++
	return &workspace.ModelVoiceCatalog{ChannelID: id, Voices: []workspace.ModelVoice{{ID: "5", Name: "voice", Source: "personal"}}}, nil
}
func TestWorkspaceModelVoicesEnvelopeAndStrictQuery(t *testing.T) {
	gin.SetMode(gin.TestMode)
	dir := t.TempDir()
	port := &voicePortFixture{}
	router := gin.New()
	router.Use(WorkspaceMiddleware(workspace.Context{ID: "local", DataDir: dir}))
	router.Use(RuntimeDependenciesMiddleware(RuntimeDependencies{ModelVoices: port}))
	RegisterWorkspaceRoutes(router.Group("/api"), app.NewLocal(nil, dir))
	good := httptest.NewRecorder()
	router.ServeHTTP(good, httptest.NewRequest("GET", "/api/workspace/model-config/voices?channelId=local", nil))
	if good.Code != http.StatusOK || port.calls != 1 || !strings.Contains(good.Body.String(), `"channelId":"local"`) {
		t.Fatal("invalid catalog envelope")
	}
	for _, suffix := range []string{"", "?channelId=", "?channelId=a&channelId=b", "?channelId=a&baseUrl=https://evil.invalid", "?channelId=a&apiKey=secret"} {
		bad := httptest.NewRecorder()
		router.ServeHTTP(bad, httptest.NewRequest("GET", "/api/workspace/model-config/voices"+suffix, nil))
		if bad.Code != 400 || port.calls != 1 {
			t.Fatal("untrusted query accepted")
		}
	}
}
