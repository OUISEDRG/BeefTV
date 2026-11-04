package handler

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"
	"infinite-canvas/backend/internal/app"
	"infinite-canvas/backend/internal/dreamaintegration"
	"infinite-canvas/backend/internal/workspace"
)

func registerDreamaIntegrationRoutes(r *gin.RouterGroup, bootstrap workspaceBootstrap) {
	group := r.Group("/integrations/dreama/v1")
	for _, path := range []string{"status", "projects", "series", "catalog", "tasks"} {
		route := path
		group.GET("/"+route, func(c *gin.Context) {
			if _, err := workspaceForLocalRequest(c, bootstrap); err != nil {
				fail(c, http.StatusUnauthorized, err)
				return
			}
			query := c.Request.URL.Query()
			channelID := ""
			params := map[string]string{}
			for key, values := range query {
				if len(values) != 1 || values[0] == "" {
					dreamaFailure(c, &dreamaintegration.Error{Status: 400, Reason: "invalid_request", Message: "请求参数缺失或重复"})
					return
				}
				if key == "channelId" {
					channelID = values[0]
				} else {
					params[key] = values[0]
				}
			}
			if channelID == "" {
				dreamaFailure(c, &dreamaintegration.Error{Status: 400, Reason: "invalid_request", Message: "请选择剧梦渠道"})
				return
			}
			result, err := dreamaintegration.New(requestProviderConfig(c, bootstrap)).Exchange(c.Request.Context(), channelID, http.MethodGet, route, params, nil)
			if err != nil {
				dreamaFailure(c, err)
				return
			}
			ok(c, result)
		})
	}
	for _, path := range []string{"series", "ensure", "catalog/refresh", "tasks/:id/cancel"} {
		route := path
		group.POST("/"+route, func(c *gin.Context) {
			if _, err := workspaceForLocalRequest(c, bootstrap); err != nil {
				fail(c, http.StatusUnauthorized, err)
				return
			}
			if len(c.Request.URL.Query()) != 0 {
				dreamaFailure(c, &dreamaintegration.Error{Status: 400, Reason: "invalid_request", Message: "写请求不接受查询参数"})
				return
			}
			data, err := io.ReadAll(http.MaxBytesReader(c.Writer, c.Request.Body, 16384))
			if err != nil {
				fail(c, 400, errors.New("请求内容过大"))
				return
			}
			var fields map[string]json.RawMessage
			if json.Unmarshal(data, &fields) != nil || fields == nil {
				fail(c, 400, errors.New("请求格式无效"))
				return
			}
			var channelID string
			if json.Unmarshal(fields["channelId"], &channelID) != nil || strings.TrimSpace(channelID) == "" {
				fail(c, 400, errors.New("请选择剧梦渠道"))
				return
			}
			delete(fields, "channelId")
			allowed := map[string]bool{}
			if route == "series" {
				allowed["projectId"] = true
				allowed["name"] = true
			}
			if route == "catalog/refresh" {
				allowed["expectedRevision"] = true
			}
			for key := range fields {
				if !allowed[key] {
					fail(c, 400, errors.New("请求包含未知字段"))
					return
				}
			}
			service := dreamaintegration.New(requestProviderConfig(c, bootstrap))
			if route == "catalog/refresh" {
				var revision int64
				if string(fields["expectedRevision"]) == "null" || json.Unmarshal(fields["expectedRevision"], &revision) != nil || revision < 0 {
					fail(c, 400, errors.New("刷新目录需要 expectedRevision"))
					return
				}
				result, e := service.Refresh(c.Request.Context(), channelID, revision)
				if e != nil {
					dreamaFailure(c, e)
					return
				}
				ok(c, result)
				return
			}
			if route == "ensure" {
				scope, e := CurrentWorkspace(c)
				if e != nil {
					failService(c, e)
					return
				}
				result, e := service.Ensure(c.Request.Context(), channelID, scope.DataDir)
				if e != nil {
					dreamaFailure(c, e)
					return
				}
				ok(c, result)
				return
			}
			upstream := route
			if route == "tasks/:id/cancel" {
				upstream = "tasks/" + c.Param("id") + "/cancel"
			}
			payload, _ := json.Marshal(fields)
			result, e := service.Exchange(c.Request.Context(), channelID, http.MethodPost, upstream, nil, payload)
			if e != nil {
				dreamaFailure(c, e)
				return
			}
			ok(c, result)
		})
	}
}
func dreamaFailure(c *gin.Context, err error) {
	var public *dreamaintegration.Error
	if errors.As(err, &public) {
		writeFailure(c, public.Status, public.Status, app.ErrorReason(public.Reason), public.Message)
		return
	}
	if errors.Is(err, workspace.ErrProviderConfigRevisionConflict) {
		fail(c, http.StatusConflict, err)
		return
	}
	failService(c, err)
}
