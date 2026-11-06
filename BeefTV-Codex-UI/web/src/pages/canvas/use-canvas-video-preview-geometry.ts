import { useLayoutEffect, type Dispatch, type SetStateAction } from "react";
import { syncCanvasVideoPreviewGeometry } from "@/lib/canvas/canvas-video-preview-geometry";
import type { AiConfig } from "@/stores/use-config-store";
import type { CanvasConnection, CanvasNodeData } from "@/types/canvas";
import type { DreamaCanvasBinding } from "@/types/dreama-workbench";

export function useCanvasVideoPreviewGeometry({ nodes, connections, config, dreamaBinding, enabled, setNodes }: {
    nodes: CanvasNodeData[];
    connections: CanvasConnection[];
    config: AiConfig;
    dreamaBinding?: DreamaCanvasBinding;
    enabled: boolean;
    setNodes: Dispatch<SetStateAction<CanvasNodeData[]>>;
}) {
    useLayoutEffect(() => {
        if (!enabled) return;
        const next = syncCanvasVideoPreviewGeometry(nodes, connections, config, dreamaBinding);
        if (next === nodes) return;
        setNodes(current => current === nodes ? next : syncCanvasVideoPreviewGeometry(current, connections, config, dreamaBinding));
    }, [nodes, connections, config, dreamaBinding, enabled, setNodes]);
}
