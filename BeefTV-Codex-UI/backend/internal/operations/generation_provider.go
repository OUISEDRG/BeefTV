package operations

import "strings"

// Standalone assistant/MCP production lacks the canvas reference compiler and
// frozen episode snapshot. Canvas proposals still run through the UI executor.
func ValidateStandaloneGenerationProtocol(protocol string) error {
	if strings.HasPrefix(strings.TrimSpace(protocol), "dreama-") {
		return Unsupported("generation_requires_canvas_binding", "剧梦生成请先调用 canvas.generation.prepare，再将 snapshotHash 和版本交给 canvas.generation.submit；或在画布内生成")
	}
	return nil
}
