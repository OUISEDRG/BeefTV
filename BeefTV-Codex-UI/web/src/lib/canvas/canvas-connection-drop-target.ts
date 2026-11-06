import { batchReferenceHandleAtY } from "@/lib/canvas/canvas-batch-table";
import { isFrameNode, isNodeHiddenByCollapsedFrame } from "@/lib/canvas/canvas-frame";
import { getConnectionTargetAnchor, isHiddenBatchChild, storyboardHandleAtY } from "@/lib/canvas/canvas-project-domain";
import { CanvasNodeType, type CanvasNodeData, type ConnectionHandle, type Position } from "@/types/canvas";

export const CONNECTION_BODY_SNAP_PADDING = 24;
const SPECIAL_PORT_SNAP_RADIUS = 56;

export type CanvasConnectionDropTarget = {
    nodeId: string | null;
    handleId?: string;
    anchorRatio?: number;
    isNearNode: boolean;
    error?: string;
};

type Options = {
    // Paint order, back to front. A direct DOM hit takes precedence over this fallback.
    nodes: CanvasNodeData[];
    point: Position;
    scale: number;
    current: ConnectionHandle;
    scriptScrollTopById: Record<string, number>;
    directNodeId?: string | null;
    blocked?: boolean;
    getError: (node: CanvasNodeData, handleId?: string) => string;
};

export function resolveCanvasConnectionDropTarget({ nodes, point, scale, current, scriptScrollTopById, directNodeId, blocked, getError }: Options): CanvasConnectionDropTarget {
    if (blocked) return { nodeId: null, isNearNode: true };
    const zoom = Math.max(scale, 0.05);
    type Hit = { node: CanvasNodeData; handleId?: string; direct: boolean; distance: number; error?: string };
    let best: Hit | undefined;
    // Frames paint behind their contents. They are sources when reverse-dragging
    // an input, and explicit invalid targets for a forward connection.
    for (const node of [...nodes.filter(isFrameNode), ...nodes.filter((item) => !isFrameNode(item))]) {
        if (isHiddenBatchChild(node, nodes) || isNodeHiddenByCollapsedFrame(node, nodes)) continue;
        const dx = Math.max(node.position.x - point.x, 0, point.x - node.position.x - node.width);
        const dy = Math.max(node.position.y - point.y, 0, point.y - node.position.y - node.height);
        const distance = Math.hypot(dx, dy) * zoom;
        const direct = directNodeId ? directNodeId === node.id : distance === 0;
        if (!direct && node.id === current.nodeId) continue;
        const special = node.type === CanvasNodeType.Script || node.type === CanvasNodeType.BatchTable;
        const scrollTop = scriptScrollTopById[node.id] || 0;
        const handleId = node.type === CanvasNodeType.Script ? storyboardHandleAtY(node, point.y, scrollTop)
            : node.type === CanvasNodeType.BatchTable ? batchReferenceHandleAtY(node, point.y, SPECIAL_PORT_SNAP_RADIUS / zoom) : undefined;
        const anchor = special ? getConnectionTargetAnchor(node, current, handleId, scrollTop) : null;
        const portDistance = anchor ? Math.hypot(point.x - anchor.x, point.y - anchor.y) * zoom : 0;
        const portHit = Boolean(handleId && portDistance <= SPECIAL_PORT_SNAP_RADIUS);
        if (!direct && (special ? !portHit : distance > CONNECTION_BODY_SNAP_PADDING)) continue;
        const hit: Hit = {
            node, handleId, direct, distance: special ? portDistance : distance,
            error: special && !portHit ? "请连接到具体的分镜行、项目设定或参考列端口" : undefined,
        };
        // Body hits beat neighboring halos; ties follow the visible paint order.
        if (!best || (hit.direct && !best.direct) || (hit.direct === best.direct && (hit.direct || hit.distance <= best.distance))) best = hit;
    }
    if (!best) return { nodeId: null, isNearNode: false };
    return {
        nodeId: best.node.id, handleId: best.handleId, isNearNode: true,
        error: best.error || getError(best.node, best.handleId) || undefined,
    };
}
