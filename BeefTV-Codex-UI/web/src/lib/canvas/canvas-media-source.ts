import { extractOwnedResourceId } from "@/lib/canvas/canvas-resource-id";
import type { CanvasNodeData } from "@/types/canvas";

/** A saved media locator survives reloads even when its temporary URL does not. */
export function hasCanvasMediaSource(node: Pick<CanvasNodeData, "metadata">): boolean {
    if (node.metadata?.content?.trim()) return true;
    const key = node.metadata?.storageKey?.trim() || "";
    if (key.startsWith("resource:")) return Boolean(extractOwnedResourceId(key));
    // LocalForage keys are opaque. Existing imports need not use today's upload prefixes.
    return Boolean(key);
}
