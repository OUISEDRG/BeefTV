package handler

import (
	"errors"
	"github.com/gin-gonic/gin"
	"infinite-canvas/backend/internal/app"
	"infinite-canvas/backend/internal/workspace"
	"net/http"
)

func registerWorkspaceModelVoiceRoutes(r *gin.RouterGroup, bootstrap workspaceBootstrap) {
	r.GET("/workspace/model-config/voices", func(c *gin.Context) {
		if _, err := workspaceForLocalRequest(c, bootstrap); err != nil {
			fail(c, http.StatusUnauthorized, err)
			return
		}
		query := c.Request.URL.Query()
		ids := query["channelId"]
		if len(query) != 1 || len(ids) != 1 || ids[0] == "" {
			fail(c, http.StatusBadRequest, errors.New("请提供一个已保存的音频渠道 channelId"))
			return
		}
		catalog, err := requestModelVoices(c, bootstrap).ReadModelVoices(c.Request.Context(), ids[0])
		if err != nil {
			var public *workspace.ModelVoiceCatalogError
			if errors.As(err, &public) {
				writeFailure(c, public.Status, public.Status, app.ErrorReason(public.Reason), public.Message)
			} else {
				failService(c, err)
			}
			return
		}
		ok(c, catalog)
	})
}
