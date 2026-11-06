import { nanoid } from "nanoid";

import { resetGenerationTaskMetadata } from "@/lib/canvas/canvas-project-generation";
import type { CanvasNodeData, CanvasNodeMetadata } from "@/types/canvas";

export function snapshotMediaVersionMetadata(metadata: CanvasNodeMetadata | undefined, rootId: string, label: string): CanvasNodeMetadata {
    const snapshot = { ...resetGenerationTaskMetadata(metadata, "success"), versionOfNodeId: rootId, versionLabel: label, versionPrimary: false };
    delete snapshot.isBatchRoot;
    delete snapshot.batchRootId;
    delete snapshot.batchChildIds;
    delete snapshot.generationOutputNodeIds;
    delete snapshot.batchFailedCount;
    delete snapshot.primaryImageId;
    delete snapshot.imageBatchExpanded;
    delete snapshot.imageBatchAsVersions;
    delete snapshot.generationEffectKeys;
    delete snapshot.generationBatches;
    delete snapshot.workflowSlot;
    snapshot.status = snapshot.content || snapshot.storageKey ? "success" : "idle";
    return snapshot;
}

export function mediaVersionNumber(label = "A") {
    return /^[A-Z]+$/.test(label) ? [...label].reduce((value, letter) => value * 26 + letter.charCodeAt(0) - 64, 0) : 1;
}

function nextMediaVersionLabel(nodes: CanvasNodeData[], rootId: string) {
    let number = Math.max(1, ...nodes.filter(node => (node.metadata?.versionOfNodeId || node.id) === rootId).map(node => mediaVersionNumber(node.metadata?.versionLabel))) + 1;
    let label = "";
    while (number > 0) {
        number--;
        label = String.fromCharCode(65 + number % 26) + label;
        number = Math.floor(number / 26);
    }
    return label;
}

// 原位生成保留稳定节点 ID，同时把生成前媒体快照放入同一版本族。
export function prepareInPlaceMediaVersion(nodes: CanvasNodeData[], nodeId: string): CanvasNodeData[] {
    const source = nodes.find((node) => node.id === nodeId);
    if (!source?.metadata?.content && !source?.metadata?.storageKey) return nodes;
    if (!source?.metadata || source.metadata.mediaVersionOwnerId) return nodes;
    const rootId = source.metadata.versionOfNodeId || source.id;
    const snapshotLabel = source.metadata.versionLabel || "A";
    // 视频重试可能仍显示上次成功封面；同一结果只归档一次。
    const duplicate = nodes.find(node => node.metadata?.mediaVersionOwnerId === source.id && (
        source.metadata?.storageKey ? node.metadata?.storageKey === source.metadata.storageKey : node.metadata?.content === source.metadata?.content
    ));
    const nextLabel = nextMediaVersionLabel(nodes, rootId);
    if (duplicate) {
        // A batch slot already archives the current result. Reserve a fresh label
        // for the next generation only if this owner has not reserved one yet.
        if (duplicate.metadata?.versionLabel !== snapshotLabel) return nodes;
        return nodes.map(node => node.id === source.id ? { ...node, metadata: { ...node.metadata, versionOfNodeId: rootId, versionLabel: nextLabel, versionPrimary: true } } : node);
    }
    const snapshot: CanvasNodeData = {
        ...source,
        id: nanoid(),
        title: `${source.title.replace(/ · [A-Z]$/, "")} · ${snapshotLabel}`,
        position: { x: source.position.x + 36, y: source.position.y + 36 },
        parentId: undefined,
        metadata: { ...snapshotMediaVersionMetadata(source.metadata, rootId, snapshotLabel), mediaVersionOwnerId: source.id },
    };
    return [
        ...nodes.map((node) => {
            if ((node.metadata?.versionOfNodeId || node.id) !== rootId) return node;
            if (node.id !== source.id) return { ...node, metadata: { ...node.metadata, versionPrimary: false } };
            return { ...node, metadata: { ...node.metadata, versionOfNodeId: rootId, versionLabel: nextLabel, versionPrimary: true } };
        }),
        snapshot,
    ];
}
