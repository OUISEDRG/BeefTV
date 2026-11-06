import { isCanvasNodeGenerating } from "@/lib/canvas/canvas-node-task-state";
import { mediaVersionNumber } from "@/lib/canvas/canvas-media-versions";
import { CanvasNodeType, type CanvasNodeData, type CanvasNodeMetadata } from "@/types/canvas";

const projectedMediaKeys = ["content", "storageKey", "mimeType", "bytes", "naturalWidth", "naturalHeight", "assetId"] as const;

function versionLabel(number: number) {
    let label = "";
    while (number > 0) {
        number--;
        label = String.fromCharCode(65 + number % 26) + label;
        number = Math.floor(number / 26);
    }
    return label;
}

function hasMedia(node: CanvasNodeData) {
    return Boolean(node.metadata?.content || node.metadata?.storageKey);
}

function completedTime(node: CanvasNodeData) {
    const parsed = Date.parse(node.metadata?.taskCompletedAt || "");
    return Number.isFinite(parsed) ? parsed : 0;
}

/** Each hidden slot keeps its task identity; the visible node only projects a selected result. */
export function reconcileCanvasImageVersionBatches(nodes: CanvasNodeData[]): CanvasNodeData[] {
    let next = nodes;
    const replaceMetadata = (id: string, patch: Partial<CanvasNodeMetadata>) => {
        const index = next.findIndex(node => node.id === id);
        if (index < 0) return;
        const node = next[index];
        if (Object.entries(patch).every(([key, value]) => node.metadata?.[key as keyof CanvasNodeMetadata] === value)) return;
        if (next === nodes) next = nodes.slice();
        next[index] = { ...node, metadata: { ...node.metadata, ...patch } };
    };

    for (const originalRoot of nodes) {
        if (originalRoot.type !== CanvasNodeType.Image || !originalRoot.metadata?.imageBatchAsVersions) continue;
        const root = next.find(node => node.id === originalRoot.id)!;
        const versions = next.filter(node => node.type === CanvasNodeType.Image && node.metadata?.mediaVersionOwnerId === root.id);
        const listed = new Set(root.metadata?.batchChildIds || []);
        const slots = versions.filter(node => node.metadata?.batchRootId === root.id && (!listed.size || listed.has(node.id)));
        if (!slots.length) continue;
        const successful = slots.filter(node => node.metadata?.status === "success" && hasMedia(node));
        let lastNumber = Math.max(
            hasMedia(root) ? mediaVersionNumber(root.metadata?.versionLabel) : 0,
            ...versions.filter(node => node.metadata?.versionLabel).map(node => mediaVersionNumber(node.metadata?.versionLabel)),
        );
        const newlyCompleted = successful.filter(node => !node.metadata?.versionLabel).sort((a, b) => completedTime(a) - completedTime(b));
        for (const slot of newlyCompleted) {
            replaceMetadata(slot.id, { versionLabel: versionLabel(++lastNumber), versionOfNodeId: root.id, versionPrimary: false });
        }
        const latest = successful.map(node => next.find(item => item.id === node.id)!)
            .sort((a, b) => mediaVersionNumber(b.metadata?.versionLabel) - mediaVersionNumber(a.metadata?.versionLabel))[0];
        const pending = slots.some(node => isCanvasNodeGenerating(node));
        const failures = slots.filter(node => node.metadata?.status === "error");
        const patch: Partial<CanvasNodeMetadata> = {
            status: pending ? "loading" : successful.length ? "success" : "error",
            batchFailedCount: failures.length,
            imageBatchExpanded: false,
            taskId: undefined,
            taskOutcome: undefined,
            taskStatus: undefined,
            taskProgress: undefined,
            taskStage: undefined,
            taskClientOperationId: undefined,
            generationSubmissionState: undefined,
            taskOutputIndex: undefined,
        };
        if (latest && !root.metadata?.mediaVersionSelected) {
            for (const key of projectedMediaKeys) Object.assign(patch, { [key]: latest.metadata?.[key] });
            Object.assign(patch, { primaryImageId: latest.id, versionOfNodeId: root.id, versionLabel: latest.metadata?.versionLabel, versionPrimary: true });
        }
        if (successful.length || pending) {
            Object.assign(patch, { errorDetails: undefined, generationErrorCode: undefined, generationErrorSummary: undefined, generationProviderCode: undefined, failedPromptFingerprint: undefined, failedInputFingerprint: undefined });
        } else {
            patch.errorDetails = failures[0]?.metadata?.errorDetails || "全部图片生成失败";
        }
        replaceMetadata(root.id, patch);
    }
    return next;
}
