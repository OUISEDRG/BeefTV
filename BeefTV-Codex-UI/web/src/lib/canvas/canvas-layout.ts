import { CanvasNodeType, type CanvasConnection, type CanvasNodeData, type Position } from "@/types/canvas";
import { getFrameDescendantIds, isFrameNode, isNodeHiddenByCollapsedFrame } from "./canvas-frame";
import { isHiddenBatchChild } from "./canvas-project-domain";
import { fitImageNodeToDecodedSize, fitVideoNodeToDecodedSize } from "./canvas-node-size";

export type CanvasLayoutMode = "row" | "column" | "grid";
export type CanvasAlignmentMode = "left" | "centerX" | "right" | "top" | "centerY" | "bottom" | "distributeX" | "distributeY";
export type CanvasLayoutLane = "text" | "image" | "video" | "audio";

const FLOW_COLUMN_GAP = 120;
const LANE_ORDER: CanvasLayoutLane[] = ["text", "image", "video", "audio"];
export type CanvasLayoutOptions = { viewportScale?: number };

export function canvasLayoutSpacing(viewportScale = 1) {
    const zoom = Number.isFinite(viewportScale) ? Math.max(0.05, viewportScale) : 1;
    return { horizontal: Math.max(80, 24 / zoom), vertical: Math.max(140, (32 + 16) / zoom), minimumTitleWidth: 160 / zoom };
}

function layoutWidth(node: CanvasNodeData, options?: CanvasLayoutOptions) {
    return Math.max(node.width, canvasLayoutSpacing(options?.viewportScale).minimumTitleWidth);
}

/** Dimensions are resolved before placement; columns and rows use their actual largest card. */
export function layoutCanvasNodeGrid(nodes: CanvasNodeData[], origin: Position, columns: number, options?: CanvasLayoutOptions) {
    const result = new Map<string, Position>();
    if (!nodes.length) return result;
    const count = Math.max(1, Math.min(nodes.length, Math.floor(columns) || 1));
    const { horizontal, vertical } = canvasLayoutSpacing(options?.viewportScale);
    const columnWidths = Array.from({ length: count }, () => 0);
    const rowHeights = Array.from({ length: Math.ceil(nodes.length / count) }, () => 0);
    nodes.forEach((node, index) => {
        columnWidths[index % count] = Math.max(columnWidths[index % count], layoutWidth(node, options));
        rowHeights[Math.floor(index / count)] = Math.max(rowHeights[Math.floor(index / count)], node.height);
    });
    let y = origin.y;
    rowHeights.forEach((height, row) => {
        let x = origin.x;
        columnWidths.forEach((width, column) => {
            const node = nodes[row * count + column];
            if (node) result.set(node.id, { x, y });
            x += width + horizontal;
        });
        y += height + vertical;
    });
    return result;
}

export function prepareCanvasLayoutNodes(nodes: CanvasNodeData[]) {
    return nodes.map((node) => {
        const { naturalWidth, naturalHeight } = node.metadata || {};
        if (!naturalWidth || !naturalHeight) return node;
        if (node.type === CanvasNodeType.Image) return fitImageNodeToDecodedSize(node, naturalWidth, naturalHeight);
        if (node.type === CanvasNodeType.Video) return fitVideoNodeToDecodedSize(node, naturalWidth, naturalHeight);
        return node;
    });
}

/** Move the planned block, not existing content, to the closest clear area below or to the right. */
export function avoidCanvasLayoutObstacles(nodes: CanvasNodeData[], positions: Map<string, Position>, allNodes: CanvasNodeData[], options?: CanvasLayoutOptions) {
    const moving = nodes.filter((node) => positions.has(node.id));
    if (!moving.length) return positions;
    const movingIds = new Set(moving.map((node) => node.id));
    const obstacles = allNodes.filter((node) => !movingIds.has(node.id) && !isHiddenBatchChild(node, allNodes) && !isNodeHiddenByCollapsedFrame(node, allNodes)
        && !(isFrameNode(node) && [...getFrameDescendantIds(node.id, allNodes)].some((id) => movingIds.has(id))));
    if (!obstacles.length) return positions;
    const left = Math.min(...moving.map((node) => positions.get(node.id)!.x));
    const top = Math.min(...moving.map((node) => positions.get(node.id)!.y));
    const width = Math.max(...moving.map((node) => positions.get(node.id)!.x + layoutWidth(node, options))) - left;
    const height = Math.max(...moving.map((node) => positions.get(node.id)!.y + node.height)) - top;
    const { horizontal, vertical } = canvasLayoutSpacing(options?.viewportScale);
    const clear = (direction: "right" | "down") => {
        const point = { x: left, y: top };
        for (let attempt = 0; attempt <= obstacles.length; attempt++) {
            const collisions = obstacles.filter((node) => point.x + width + horizontal > node.position.x && point.x < node.position.x + layoutWidth(node, options) + horizontal
                && point.y + height + vertical > node.position.y && point.y < node.position.y + node.height + vertical);
            if (!collisions.length) break;
            if (direction === "right") point.x = Math.max(...collisions.map((node) => node.position.x + layoutWidth(node, options) + horizontal));
            else point.y = Math.max(...collisions.map((node) => node.position.y + node.height + vertical));
        }
        return point;
    };
    const right = clear("right");
    const down = clear("down");
    const origin = right.x - left < down.y - top ? right : down;
    if (origin.x === left && origin.y === top) return positions;
    return new Map([...positions].map(([id, position]) => [id, { x: position.x + origin.x - left, y: position.y + origin.y - top }]));
}

export function layoutCanvasInsertedNodes(nodes: CanvasNodeData[], center: Position, allNodes: CanvasNodeData[] = [], options?: CanvasLayoutOptions & { columns?: number }) {
    if (!nodes.length) return nodes;
    const prepared = prepareCanvasLayoutNodes(nodes);
    const origin = { x: center.x - prepared[0].width / 2, y: center.y - prepared[0].height / 2 };
    const grid = layoutCanvasNodeGrid(prepared, origin, options?.columns || 4, options);
    const positions = avoidCanvasLayoutObstacles(prepared, grid, allNodes, options);
    return prepared.map((node) => ({ ...node, position: positions.get(node.id)! }));
}

export function applyCanvasNodeLayout(allNodes: CanvasNodeData[], prepared: CanvasNodeData[], positions: Map<string, Position>, options?: CanvasLayoutOptions) {
    const placed = avoidCanvasLayoutObstacles(prepared, positions, allNodes, options);
    const byId = new Map(prepared.map((node) => [node.id, node]));
    return allNodes.map((node) => placed.has(node.id) ? { ...(byId.get(node.id) || node), position: placed.get(node.id)! } : node);
}

export function layoutCanvasNodes(nodes: CanvasNodeData[], mode: CanvasLayoutMode, options?: CanvasLayoutOptions) {
    if (!nodes.length) return new Map<string, Position>();
    const sorted = [...nodes].sort((a, b) => a.position.y - b.position.y || a.position.x - b.position.x);
    const left = Math.min(...sorted.map((node) => node.position.x));
    const top = Math.min(...sorted.map((node) => node.position.y));
    const { horizontal, vertical } = canvasLayoutSpacing(options?.viewportScale);
    const result = new Map<string, Position>();

    if (mode === "row") {
        let x = left;
        sorted.forEach((node) => {
            result.set(node.id, { x, y: top });
            x += layoutWidth(node, options) + horizontal;
        });
        return result;
    }

    if (mode === "column") {
        let y = top;
        sorted.forEach((node) => {
            result.set(node.id, { x: left, y });
            y += node.height + vertical;
        });
        return result;
    }

    const columns = Math.ceil(Math.sqrt(sorted.length));
    return layoutCanvasNodeGrid(sorted, { x: left, y: top }, columns, options);
}

export function layoutCanvasFlow(nodes: CanvasNodeData[], connections: CanvasConnection[], options?: CanvasLayoutOptions) {
    if (!nodes.length) return new Map<string, Position>();
    const { horizontal, vertical } = canvasLayoutSpacing(options?.viewportScale);
    const selectedIds = new Set(nodes.map((node) => node.id));
    const inbound = new Map(nodes.map((node) => [node.id, 0]));
    const outbound = new Map(nodes.map((node) => [node.id, [] as string[]]));

    connections.forEach((connection) => {
        if (!selectedIds.has(connection.fromNodeId) || !selectedIds.has(connection.toNodeId)) return;
        inbound.set(connection.toNodeId, (inbound.get(connection.toNodeId) || 0) + 1);
        outbound.get(connection.fromNodeId)?.push(connection.toNodeId);
    });

    // Kahn 拓扑分层让依赖方向保持从左到右；环形连接留在第一层，避免布局死循环。
    const queue = nodes.filter((node) => !inbound.get(node.id)).map((node) => node.id);
    const layerById = new Map<string, number>();
    queue.forEach((id) => layerById.set(id, 0));
    while (queue.length) {
        const id = queue.shift()!;
        const layer = layerById.get(id) || 0;
        outbound.get(id)?.forEach((target) => {
            layerById.set(target, Math.max(layerById.get(target) || 0, layer + 1));
            inbound.set(target, (inbound.get(target) || 1) - 1);
            if (!inbound.get(target)) queue.push(target);
        });
    }
    nodes.forEach((node) => {
        if (!layerById.has(node.id)) layerById.set(node.id, 0);
    });

    const left = Math.min(...nodes.map((node) => node.position.x));
    const top = Math.min(...nodes.map((node) => node.position.y));
    const result = new Map<string, Position>();
    const layers = [...new Set(layerById.values())].sort((a, b) => a - b);
    const layerX = new Map<number, number>();
    let x = left;
    layers.forEach((layer) => {
        const layerNodes = nodes.filter((node) => layerById.get(node.id) === layer);
        layerX.set(layer, x);
        x += Math.max(...layerNodes.map((node) => layoutWidth(node, options))) + Math.max(FLOW_COLUMN_GAP, horizontal);
    });

    let laneTop = top;
    LANE_ORDER.forEach((lane) => {
        const laneNodes = nodes.filter((node) => canvasLayoutLane(node) === lane);
        if (!laneNodes.length) return;
        const stacks = new Map<number, CanvasNodeData[]>();
        laneNodes.forEach((node) => {
            const layer = layerById.get(node.id) || 0;
            stacks.set(layer, [...(stacks.get(layer) || []), node]);
        });
        let laneHeight = 0;
        stacks.forEach((stack) => {
            const height = stack.reduce((sum, node) => sum + node.height, 0) + Math.max(0, stack.length - 1) * vertical;
            laneHeight = Math.max(laneHeight, height);
        });
        stacks.forEach((stack, layer) => {
            let y = laneTop;
            stack.sort((a, b) => a.position.y - b.position.y || a.position.x - b.position.x).forEach((node) => {
                result.set(node.id, { x: layerX.get(layer) || left, y });
                y += node.height + vertical;
            });
        });
        laneTop += laneHeight + vertical;
    });
    return result;
}

export function layoutCanvasNodesByMediaType(nodes: CanvasNodeData[], options?: CanvasLayoutOptions) {
    if (!nodes.length) return new Map<string, Position>();
    const left = Math.min(...nodes.map((node) => node.position.x));
    const top = Math.min(...nodes.map((node) => node.position.y));
    const result = new Map<string, Position>();
    const { vertical } = canvasLayoutSpacing(options?.viewportScale);
    let laneTop = top;

    LANE_ORDER.forEach((lane) => {
        const laneNodes = nodes.filter((node) => canvasLayoutLane(node) === lane).sort((a, b) => a.position.y - b.position.y || a.position.x - b.position.x);
        if (!laneNodes.length) return;
        const columns = Math.min(4, Math.max(1, Math.ceil(Math.sqrt(laneNodes.length))));
        const positions = layoutCanvasNodeGrid(laneNodes, { x: left, y: laneTop }, columns, options);
        positions.forEach((position, id) => result.set(id, position));
        laneTop = Math.max(...laneNodes.map((node) => positions.get(node.id)!.y + node.height)) + vertical;
    });
    return result;
}

export function layoutCanvasAuto(nodes: CanvasNodeData[], connections: CanvasConnection[], options?: CanvasLayoutOptions) {
    const candidates = nodes.filter((node) => !node.metadata?.locked && !isFrameNode(node));
    if (candidates.length < 2) return new Map<string, Position>();
    const candidateIds = new Set(candidates.map((node) => node.id));
    const hasConnections = connections.some((connection) => candidateIds.has(connection.fromNodeId) && candidateIds.has(connection.toNodeId));
    return hasConnections ? layoutCanvasFlow(candidates, connections, options) : layoutCanvasNodesByMediaType(candidates, options);
}

export const CANVAS_SPREAD_SCALE = 1.4;
export const CANVAS_SPREAD_MIN_GAP = 56;

/**
 * 保持选区里节点的相对占位，只把彼此间距拉开。
 * 先按选区左上角等比展开，再把仍然过近的盒子沿原有主方向推开，最后把整组钉回原来的左上角。
 */
export function spreadCanvasNodes(nodes: CanvasNodeData[], options?: CanvasLayoutOptions & { scale?: number; minGap?: number }) {
    const result = new Map<string, Position>();
    if (nodes.length < 2) return result;

    const scale = options?.scale ?? CANVAS_SPREAD_SCALE;
    const minGap = options?.minGap ?? CANVAS_SPREAD_MIN_GAP;
    const spacing = canvasLayoutSpacing(options?.viewportScale);
    const gapX = Math.max(minGap, spacing.horizontal);
    const gapY = Math.max(minGap, spacing.vertical);
    const originX = Math.min(...nodes.map((node) => node.position.x));
    const originY = Math.min(...nodes.map((node) => node.position.y));
    const items = nodes.map((node) => ({
        id: node.id,
        width: layoutWidth(node, options),
        height: node.height,
        x: originX + (node.position.x - originX) * scale,
        y: originY + (node.position.y - originY) * scale,
    }));

    for (let pass = 0; pass < 12; pass++) {
        let moved = false;
        for (let i = 0; i < items.length; i++) {
            for (let j = i + 1; j < items.length; j++) {
                const first = items[i]!;
                const second = items[j]!;
                const overlapX = Math.min(first.x + first.width + gapX, second.x + second.width + gapX) - Math.max(first.x, second.x);
                const overlapY = Math.min(first.y + first.height + gapY, second.y + second.height + gapY) - Math.max(first.y, second.y);
                if (overlapX <= 0 || overlapY <= 0) continue;

                const centerDx = first.x + first.width / 2 - (second.x + second.width / 2);
                const centerDy = first.y + first.height / 2 - (second.y + second.height / 2);
                if (Math.abs(centerDx) >= Math.abs(centerDy)) {
                    const direction = centerDx === 0 ? (first.id < second.id ? 1 : -1) : centerDx > 0 ? 1 : -1;
                    const push = overlapX / 2;
                    first.x += direction * push;
                    second.x -= direction * push;
                } else {
                    const direction = centerDy === 0 ? (first.id < second.id ? 1 : -1) : centerDy > 0 ? 1 : -1;
                    const push = overlapY / 2;
                    first.y += direction * push;
                    second.y -= direction * push;
                }
                moved = true;
            }
        }
        if (!moved) break;
    }

    const nextOriginX = Math.min(...items.map((item) => item.x));
    const nextOriginY = Math.min(...items.map((item) => item.y));
    const deltaX = originX - nextOriginX;
    const deltaY = originY - nextOriginY;
    items.forEach((item) => result.set(item.id, { x: item.x + deltaX, y: item.y + deltaY }));
    return result;
}

export function canvasLayoutLane(node: CanvasNodeData): CanvasLayoutLane {
    if (node.type === CanvasNodeType.Video) return "video";
    if (node.type === CanvasNodeType.Audio) return "audio";
    if (node.type === CanvasNodeType.Image || node.type === CanvasNodeType.Drawing || node.type === CanvasNodeType.Panorama || node.type === CanvasNodeType.Compare || node.type === CanvasNodeType.ColorGrade) return "image";
    return "text";
}

export function alignCanvasNodes(nodes: CanvasNodeData[], mode: CanvasAlignmentMode) {
    const result = new Map<string, Position>();
    if (nodes.length < 2) return result;
    const left = Math.min(...nodes.map((node) => node.position.x));
    const top = Math.min(...nodes.map((node) => node.position.y));
    const right = Math.max(...nodes.map((node) => node.position.x + node.width));
    const bottom = Math.max(...nodes.map((node) => node.position.y + node.height));
    const centerX = (left + right) / 2;
    const centerY = (top + bottom) / 2;

    if (mode === "distributeX") {
        const sorted = [...nodes].sort((a, b) => a.position.x - b.position.x);
        const totalWidth = sorted.reduce((sum, node) => sum + node.width, 0);
        const gap = (right - left - totalWidth) / Math.max(1, sorted.length - 1);
        let x = left;
        sorted.forEach((node) => {
            result.set(node.id, { x, y: node.position.y });
            x += node.width + gap;
        });
        return result;
    }

    if (mode === "distributeY") {
        const sorted = [...nodes].sort((a, b) => a.position.y - b.position.y);
        const totalHeight = sorted.reduce((sum, node) => sum + node.height, 0);
        const gap = (bottom - top - totalHeight) / Math.max(1, sorted.length - 1);
        let y = top;
        sorted.forEach((node) => {
            result.set(node.id, { x: node.position.x, y });
            y += node.height + gap;
        });
        return result;
    }

    nodes.forEach((node) => {
        const x = mode === "left" ? left : mode === "centerX" ? centerX - node.width / 2 : mode === "right" ? right - node.width : node.position.x;
        const y = mode === "top" ? top : mode === "centerY" ? centerY - node.height / 2 : mode === "bottom" ? bottom - node.height : node.position.y;
        result.set(node.id, { x, y });
    });
    return result;
}

export function nextCanvasVersionLabel(rootId: string, nodes: CanvasNodeData[]) {
    const labels = nodes
        .filter((node) => (node.metadata?.versionOfNodeId || node.id) === rootId)
        .map((node) => node.metadata?.versionLabel)
        .filter((label): label is string => Boolean(label));
    if (!labels.length) return "B";
    const highest = Math.max(...labels.map((label) => label.charCodeAt(0)), "A".charCodeAt(0));
    return String.fromCharCode(Math.min("Z".charCodeAt(0), highest + 1));
}
