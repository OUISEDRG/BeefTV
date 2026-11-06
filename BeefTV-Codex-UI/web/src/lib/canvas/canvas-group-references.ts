import { batchSourceRestriction, planBatchConnections, type BatchConnectionPlan, type BatchConnectionPlanOptions, type BatchConnectionSkip } from "./canvas-batch-connection";
import { hasCanvasMediaSource } from "./canvas-media-source";
import { getCanvasReferenceReceiverId } from "./canvas-resource-references";
import { getNodeResourceKind } from "./node-registry";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";

export type CanvasReferenceSources = {
    sourceNodeIds: string[];
    skipped: BatchConnectionSkip[];
    groupNodeIds: string[];
};

export function canvasReferenceSourceRestriction(node: CanvasNodeData) {
    const restriction = batchSourceRestriction(node);
    if (restriction) return restriction;
    if (node.type === CanvasNodeType.Workflow || node.metadata?.reusableWorkflow) return "工作流不能作为整组参考素材";
    if (node.metadata?.mediaVersionOwnerId) return "历史版本不作为整组参考素材";
    if (node.metadata?.status === "loading" || node.metadata?.fileUpload === "uploading") return "素材尚未就绪";
    if (node.type === CanvasNodeType.Image || node.type === CanvasNodeType.Video || node.type === CanvasNodeType.Audio) {
        return hasCanvasMediaSource(node) ? "" : "节点没有可引用的媒体素材";
    }
    if (node.metadata?.workflowKind === "character" && node.metadata.characterAssetId) return "";
    const kind = getNodeResourceKind(node);
    if (kind === "text") return node.metadata?.content?.trim() || node.metadata?.prompt?.trim() || node.metadata?.skillSnapshot ? "" : "文本内容为空";
    return kind ? "" : "节点没有可引用的素材";
}

function compareGroupMembers(first: CanvasNodeData, second: CanvasNodeData) {
    const shot = (node: CanvasNodeData) => Number.isFinite(node.metadata?.shotIndex) && node.metadata!.shotIndex! >= 0 ? node.metadata!.shotIndex! : Infinity;
    const firstShot = shot(first), secondShot = shot(second);
    if (firstShot !== secondShot) return firstShot - secondShot;
    return first.position.y - second.position.y || first.position.x - second.position.x || (first.id < second.id ? -1 : first.id > second.id ? 1 : 0);
}

/** Groups are source selectors, not graph vertices. Collapsing a group never changes its references. */
export function resolveCanvasReferenceSources(sourceNodeIds: readonly string[], nodes: CanvasNodeData[]): CanvasReferenceSources {
    const byId = new Map(nodes.map(node => [node.id, node]));
    const children = new Map<string, CanvasNodeData[]>();
    nodes.forEach(node => {
        if (!node.parentId) return;
        children.set(node.parentId, [...(children.get(node.parentId) || []), node]);
    });
    const result: CanvasReferenceSources = { sourceNodeIds: [], skipped: [], groupNodeIds: [] };
    const resolved = new Set<string>();
    const visitedGroups = new Set<string>();
    for (const id of sourceNodeIds) {
        const source = byId.get(id);
        if (!source) {
            if (!resolved.has(id)) result.skipped.push({ nodeId: id, reason: "找不到连接源节点" });
            resolved.add(id);
            continue;
        }
        if (source.type !== CanvasNodeType.Frame) {
            if (!resolved.has(id)) result.sourceNodeIds.push(id);
            resolved.add(id);
            continue;
        }
        if (visitedGroups.has(id)) continue;
        const members: CanvasNodeData[] = [];
        const pending = [source.id];
        for (let index = 0; index < pending.length; index++) {
            const groupId = pending[index];
            if (visitedGroups.has(groupId)) continue;
            visitedGroups.add(groupId);
            result.groupNodeIds.push(groupId);
            for (const child of children.get(groupId) || []) {
                if (child.type === CanvasNodeType.Frame) pending.push(child.id);
                else members.push(child);
            }
        }
        if (!members.length) result.skipped.push({ nodeId: source.id, reason: "分组内没有可引用的素材" });
        for (const member of members.sort(compareGroupMembers)) {
            if (resolved.has(member.id)) continue;
            resolved.add(member.id);
            const reason = canvasReferenceSourceRestriction(member);
            if (reason) result.skipped.push({ nodeId: member.id, reason });
            else result.sourceNodeIds.push(member.id);
        }
    }
    return result;
}

export function resolveCanvasGroupReferenceSources(sourceGroupId: string, nodes: CanvasNodeData[]): CanvasReferenceSources {
    if (!nodes.some(node => node.id === sourceGroupId && node.type === CanvasNodeType.Frame)) {
        return { sourceNodeIds: [], groupNodeIds: [], skipped: [{ nodeId: sourceGroupId, reason: "找不到可引用的分组" }] };
    }
    return resolveCanvasReferenceSources([sourceGroupId], nodes);
}

export function planCanvasReferenceConnections(options: BatchConnectionPlanOptions): BatchConnectionPlan {
    const sources = resolveCanvasReferenceSources(options.sourceNodeIds, options.nodes);
    return planResolvedReferenceConnections({ ...options, allowCapacityOverflow: sources.groupNodeIds.length ? false : options.allowCapacityOverflow }, sources);
}

export function planCanvasGroupConnections(options: Omit<BatchConnectionPlanOptions, "sourceNodeIds"> & { sourceGroupId: string }): BatchConnectionPlan {
    const sources = resolveCanvasGroupReferenceSources(options.sourceGroupId, options.nodes);
    return planResolvedReferenceConnections({ ...options, sourceNodeIds: sources.sourceNodeIds, allowCapacityOverflow: false }, sources);
}

function planResolvedReferenceConnections(options: BatchConnectionPlanOptions, sources: CanvasReferenceSources): BatchConnectionPlan {
    if (getCanvasReferenceReceiverId(options.targetNodeId, options.nodes, options.connections) !== options.targetNodeId) {
        return { connected: [], duplicates: [], connections: [], skipped: [...sources.skipped, ...sources.sourceNodeIds.map(nodeId => ({
            nodeId, reason: "此节点通过生成配置接收素材，请连接到对应的生成配置节点",
        }))] };
    }
    const plan = planBatchConnections({ ...options, sourceNodeIds: sources.sourceNodeIds });
    return { ...plan, skipped: [...sources.skipped, ...plan.skipped] };
}
