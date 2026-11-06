import { NODE_DEFAULT_SIZE } from "@/constant/canvas";
import { buildCanvasGenerationRequirements, buildGenerationConfig } from "@/lib/canvas/canvas-generation-config";
import { hasMediaResource } from "@/lib/canvas/canvas-node-semantics";
import { nodeSizeFromRatio } from "@/lib/canvas/canvas-node-size";
import { buildCanvasNodeMentionReferenceMap } from "@/lib/canvas/canvas-resource-references";
import { dreamaCanvasVideoConfig } from "@/lib/canvas/dreama-workbench";
import type { AiConfig } from "@/stores/use-config-store";
import { CanvasNodeType, type CanvasConnection, type CanvasNodeData } from "@/types/canvas";
import type { DreamaCanvasBinding } from "@/types/dreama-workbench";

/** Empty previews inherit the same effective settings as the prompt panel without saving those defaults on the node. */
export function syncCanvasVideoPreviewGeometry(nodes: CanvasNodeData[], connections: CanvasConnection[], config: AiConfig, dreamaBinding?: DreamaCanvasBinding): CanvasNodeData[] {
    const previews = nodes.filter(node => node.type === CanvasNodeType.Video && !hasMediaResource(node)
        && !node.metadata?.manualSize && !node.metadata?.freeResize && !node.metadata?.locked && !node.metadata?.workflowSlot);
    if (!previews.length) return nodes;
    const referencesByNodeId = buildCanvasNodeMentionReferenceMap(nodes, connections, previews);
    const previewIds = new Set(previews.map(node => node.id));
    const baseSize = NODE_DEFAULT_SIZE[CanvasNodeType.Video];
    let changed = false;
    const result = nodes.map(node => {
        if (!previewIds.has(node.id)) return node;
        const references = referencesByNodeId.get(node.id) || [];
        const prompt = node.metadata?.composerContent ?? node.metadata?.prompt ?? "";
        const requirements = buildCanvasGenerationRequirements(config, node, "video", {
            textCount: (prompt.trim() ? 1 : 0) + references.filter(reference => reference.active && reference.kind === "text").length,
            imageCount: references.filter(reference => reference.active && reference.kind === "image").length,
            videoCount: references.filter(reference => reference.active && reference.kind === "video").length,
            audioCount: references.filter(reference => reference.active && reference.kind === "audio").length,
            characterCount: references.filter(reference => reference.active && reference.kind === "character").length,
        });
        const nodeConfig = buildGenerationConfig(config, node, "video", requirements);
        const effectiveConfig = dreamaCanvasVideoConfig(nodeConfig, dreamaBinding, node.metadata?.size).config;
        const size = nodeSizeFromRatio(effectiveConfig.size, baseSize.width, baseSize.height);
        if (!size || (Math.abs(node.width - size.width) < 0.01 && Math.abs(node.height - size.height) < 0.01)) return node;
        changed = true;
        return {
            ...node,
            ...size,
            position: {
                x: node.position.x + (node.width - size.width) / 2,
                y: node.position.y + (node.height - size.height) / 2,
            },
        };
    });
    return changed ? result : nodes;
}
