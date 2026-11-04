import { dreamaImageToolSupport } from "@/lib/canvas/dreama-image-tool-support";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { useEffectiveConfig } from "@/stores/use-config-store";
import type { CanvasNodeData } from "@/types/canvas";

export function useDreamaImageToolSupport(toolId: string, node: CanvasNodeData, projectId?: string) {
    const config = useEffectiveConfig();
    const canvas = useCanvasStore((state) => state.projects.find((project) => project.id === projectId));
    return dreamaImageToolSupport(toolId, node, config, canvas);
}
