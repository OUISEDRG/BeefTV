import { isCanvasNodeGenerating } from "@/lib/canvas/canvas-node-task-state";
import { prepareInPlaceMediaVersion, snapshotMediaVersionMetadata } from "@/lib/canvas/canvas-media-versions";
import type { CanvasNodeData } from "@/types/canvas";

/** Swap media while retaining the node ID, connections and authored layout. */
export function selectCanvasMediaVersion(nodes: CanvasNodeData[], versionId: string): CanvasNodeData[] {
    const version = nodes.find(node => node.id === versionId);
    const ownerId = version?.metadata?.mediaVersionOwnerId;
    const owner = nodes.find(node => node.id === ownerId);
    if (!version || !owner || version.type !== owner.type || isCanvasNodeGenerating(owner)
        || !version.metadata?.content && !version.metadata?.storageKey) return nodes;
    const rootId = owner.metadata?.versionOfNodeId || owner.id;
    const sameMedia = owner.metadata?.storageKey ? owner.metadata.storageKey === version.metadata.storageKey : owner.metadata?.content === version.metadata.content;
    const hasCurrent = Boolean(owner.metadata?.content || owner.metadata?.storageKey) && !sameMedia;
    const currentMetadata = snapshotMediaVersionMetadata(owner.metadata, rootId, owner.metadata?.versionLabel || "A");
    const selectedMetadata = snapshotMediaVersionMetadata(version.metadata, rootId, version.metadata?.versionLabel || "A");
    delete selectedMetadata.mediaVersionOwnerId;
    // Generated version slots retain their task-to-result identity for recovery.
    // Selecting one projects its media onto the owner without swapping slot data.
    if (owner.metadata?.imageBatchAsVersions || nodes.some(node => node.metadata?.mediaVersionOwnerId === owner.id && node.metadata.taskId)) {
        const archived = hasCurrent ? prepareInPlaceMediaVersion(nodes, owner.id) : nodes;
        return archived.map(node => node.id !== owner.id ? node : { ...owner, metadata: {
            ...selectedMetadata,
            count: owner.metadata?.count,
            locked: owner.metadata?.locked,
            manualSize: owner.metadata?.manualSize,
            freeResize: owner.metadata?.freeResize,
            workflowSlot: owner.metadata?.workflowSlot,
            generationResultPlacement: owner.metadata?.generationResultPlacement,
            ...(owner.metadata?.imageBatchAsVersions ? {
                imageBatchAsVersions: true,
                isBatchRoot: owner.metadata.isBatchRoot,
                batchChildIds: owner.metadata.batchChildIds,
                generationOutputNodeIds: owner.metadata.generationOutputNodeIds,
                batchFailedCount: owner.metadata.batchFailedCount,
                primaryImageId: version.id,
                imageBatchExpanded: false,
            } : {}),
            versionPrimary: true,
            mediaVersionSelected: true,
        } });
    }
    return nodes.flatMap(node => {
        if (node.id === owner.id) return [{ ...owner, metadata: {
            ...selectedMetadata,
            count: owner.metadata?.count,
            locked: owner.metadata?.locked,
            manualSize: owner.metadata?.manualSize,
            freeResize: owner.metadata?.freeResize,
            workflowSlot: owner.metadata?.workflowSlot,
            generationResultPlacement: owner.metadata?.generationResultPlacement,
            versionPrimary: true,
            mediaVersionSelected: true,
        } }];
        if (node.id !== version.id) return [node];
        return hasCurrent ? [{ ...node, metadata: { ...currentMetadata, mediaVersionOwnerId: owner.id } }] : [];
    });
}
