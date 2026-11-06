import { FRAME_HEADER_HEIGHT, FRAME_PADDING, isFrameNode } from "@/lib/canvas/canvas-frame";
import { hasCanvasMediaSource } from "@/lib/canvas/canvas-media-source";
import { resolveCanvasNodeAncestry } from "@/lib/canvas/canvas-node-ancestry";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";

type StoryboardGroupMembers = {
    ok: true;
    members: CanvasNodeData[];
    parentId?: string;
    dreamaSeriesId?: number;
};

export type StoryboardGroupSelection = StoryboardGroupMembers | { ok: false; reason: string };

/** Shared by the toolbar and execution so eligibility cannot drift. */
export function selectStoryboardGroupMembers(nodes: CanvasNodeData[], selectedIds: ReadonlySet<string>, defaultSeriesId?: number): StoryboardGroupSelection {
    const members = nodes.filter(node => selectedIds.has(node.id) && !node.metadata?.locked
        && (node.type === CanvasNodeType.Image || node.type === CanvasNodeType.Video) && hasCanvasMediaSource(node))
        .sort((a, b) => a.position.y - b.position.y || a.position.x - b.position.x);
    if (members.length < 2) return { ok: false, reason: "请至少选择两个已有且未锁定的图片或视频节点" };

    const byId = new Map(nodes.map(node => [node.id, node]));
    const ancestries: string[][] = [];
    const series = new Set<number | undefined>();
    let explicitSeriesId: number | undefined;
    for (const member of members) {
        try {
            const { ancestors, dreamaSeriesId } = resolveCanvasNodeAncestry(member.id, byId);
            if (!ancestors.every(isFrameNode)) return { ok: false, reason: "节点的上级分组无效，请先修复分组关系" };
            series.add(dreamaSeriesId ?? defaultSeriesId);
            explicitSeriesId ??= dreamaSeriesId;
            ancestries.push(ancestors.map(node => node.id));
        } catch (error) {
            return { ok: false, reason: error instanceof Error ? error.message : "无法确定节点的剧集归属" };
        }
    }
    if (series.size > 1) return { ok: false, reason: "所选节点属于不同剧集，请按剧集分别创建分镜组" };
    const parentId = ancestries[0].find(id => ancestries.every(ancestors => ancestors.includes(id)));
    // Reparenting across sibling groups must retain an episode bound on either branch.
    return { ok: true, members, parentId, dreamaSeriesId: explicitSeriesId };
}

export function buildStoryboardGroupNodes(nodes: CanvasNodeData[], selection: StoryboardGroupMembers, frame: CanvasNodeData): CanvasNodeData[] {
    const { members } = selection;
    const gap = 24;
    const columns = Math.min(4, Math.ceil(Math.sqrt(members.length)));
    const rows = Math.ceil(members.length / columns);
    const cellWidth = Math.max(...members.map(node => node.width));
    const cellHeight = Math.max(...members.map(node => node.height));
    const width = FRAME_PADDING * 2 + columns * cellWidth + (columns - 1) * gap;
    const height = FRAME_HEADER_HEIGHT + FRAME_PADDING * 2 + rows * cellHeight + (rows - 1) * gap;
    const group: CanvasNodeData = {
        ...frame,
        title: `分镜组 · ${members.length} 镜`,
        parentId: selection.parentId,
        position: {
            x: Math.min(...members.map(node => node.position.x)) - FRAME_PADDING,
            y: Math.min(...members.map(node => node.position.y)) - FRAME_HEADER_HEIGHT - FRAME_PADDING,
        },
        width,
        height,
        metadata: {
            ...frame.metadata,
            workflowKind: "storyboard",
            workflowTitle: "分镜组",
            dreamaSeriesId: selection.dreamaSeriesId,
            frame: { collapsed: false, expandedWidth: width, expandedHeight: height },
        },
    };
    const memberIndices = new Map(members.map((node, index) => [node.id, index]));
    return [...nodes.map(node => {
        const index = memberIndices.get(node.id);
        if (index === undefined) return node;
        return {
            ...node,
            parentId: group.id,
            position: {
                x: group.position.x + FRAME_PADDING + (index % columns) * (cellWidth + gap) + (cellWidth - node.width) / 2,
                y: group.position.y + FRAME_HEADER_HEIGHT + FRAME_PADDING + Math.floor(index / columns) * (cellHeight + gap) + (cellHeight - node.height) / 2,
            },
            metadata: { ...node.metadata, workflowKind: node.metadata?.workflowKind || "shot", shotIndex: node.metadata?.shotIndex ?? index + 1 },
        };
    }), group];
}
