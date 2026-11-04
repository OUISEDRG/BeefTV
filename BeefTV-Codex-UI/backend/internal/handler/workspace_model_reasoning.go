package handler

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/http"

	"infinite-canvas/backend/internal/app"
	"infinite-canvas/backend/internal/workspace"

	"github.com/gin-gonic/gin"
)

func registerWorkspaceModelReasoningRoutes(r *gin.RouterGroup, bootstrap workspaceBootstrap) {
	r.GET("/workspace/model-config/reasoning", func(c *gin.Context) {
		if _, err := workspaceForLocalRequest(c, bootstrap); err != nil {
			fail(c, http.StatusUnauthorized, err)
			return
		}
		query := c.Request.URL.Query()
		channelIDs := query["channelId"]
		if len(query) != 1 || len(channelIDs) != 1 || channelIDs[0] == "" {
			fail(c, http.StatusBadRequest, errors.New("请提供一个已保存的模型渠道 channelId"))
			return
		}
		settings, err := requestModelReasoning(c, bootstrap).ReadModelReasoning(c.Request.Context(), channelIDs[0])
		if err != nil {
			failModelReasoning(c, err)
			return
		}
		ok(c, settings)
	})
	r.PUT("/workspace/model-config/reasoning", func(c *gin.Context) {
		if _, err := workspaceForLocalRequest(c, bootstrap); err != nil {
			fail(c, http.StatusUnauthorized, err)
			return
		}
		var payload struct {
			ChannelID        string          `json:"channelId"`
			Model            string          `json:"model"`
			Effort           json.RawMessage `json:"effort"`
			ExpectedRevision *int64          `json:"expectedRevision"`
		}
		decoder := json.NewDecoder(http.MaxBytesReader(c.Writer, c.Request.Body, 16<<10))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&payload); err != nil || len(payload.Effort) == 0 || payload.ExpectedRevision == nil || decoder.Decode(&struct{}{}) != io.EOF {
			fail(c, http.StatusBadRequest, errors.New("推理设置需要 channelId、model、effort 和 expectedRevision"))
			return
		}
		var effort *string
		if !bytes.Equal(bytes.TrimSpace(payload.Effort), []byte("null")) {
			var value string
			if err := json.Unmarshal(payload.Effort, &value); err != nil {
				fail(c, http.StatusBadRequest, errors.New("effort 必须为推理档位字符串，或 null 以恢复默认"))
				return
			}
			effort = &value
		}
		settings, err := requestModelReasoning(c, bootstrap).UpdateModelReasoning(c.Request.Context(), workspace.ModelReasoningUpdate{
			ChannelID: payload.ChannelID, Model: payload.Model, Effort: effort, ExpectedRevision: *payload.ExpectedRevision,
		})
		if err != nil {
			failModelReasoning(c, err)
			return
		}
		ok(c, settings)
	})
}

func failModelReasoning(c *gin.Context, err error) {
	var public *workspace.ModelReasoningError
	if errors.As(err, &public) {
		writeFailure(c, public.Status, public.Status, app.ErrorReason(public.Reason), public.Message)
		return
	}
	failService(c, err)
}
