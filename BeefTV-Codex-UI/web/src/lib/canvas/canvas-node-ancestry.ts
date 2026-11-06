import { isFrameNode } from "@/lib/canvas/canvas-frame";
import type { CanvasNodeData } from "@/types/canvas";

/** Shared by episode binding and regrouping; nearest ancestors come first. */
export function resolveCanvasNodeAncestry(nodeId: string, byId: ReadonlyMap<string, CanvasNodeData>): { ancestors: CanvasNodeData[]; dreamaSeriesId?: number } {
    let current = byId.get(nodeId);
    if (!current) throw new Error("生成节点已不存在");
    const seen = new Set<string>();
    const series = new Set<number>();
    const ancestors: CanvasNodeData[] = [];
    while (current) {
        if (seen.has(current.id)) throw new Error("剧集分组存在循环，请修复分组关系");
        seen.add(current.id);
        if (current.id !== nodeId) ancestors.push(current);
        if (current.metadata?.dreamaSeriesId !== undefined) {
            const id = current.metadata.dreamaSeriesId;
            if (!isFrameNode(current) || !Number.isSafeInteger(id) || id <= 0) throw new Error("剧集分组绑定无效，请重新选择剧集");
            series.add(id);
        }
        if (!current.parentId) break;
        current = byId.get(current.parentId);
        if (!current) throw new Error("节点的分组已不存在，无法确定剧集归属");
    }
    if (series.size > 1) throw new Error("节点属于多个不同剧集分组，请保留唯一剧集归属");
    return { ancestors, dreamaSeriesId: [...series][0] };
}
