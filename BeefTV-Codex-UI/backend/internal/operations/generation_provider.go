package operations

import "strings"

// Standalone assistant/MCP production lacks the canvas reference compiler and
// frozen episode snapshot. Canvas proposals still run through the UI executor.
func ValidateStandaloneGenerationProtocol(protocol string) error {
	if strings.HasPrefix(strings.TrimSpace(protocol), "dreama-") {
		return Unsupported("generation_requires_canvas_binding", "剧梦生成请使用画布节点或助手生成提议，在画布中确认项目、剧集及引用后执行")
	}
	return nil
}
