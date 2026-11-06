import { parseAssetRecord } from "@/lib/asset-record";
import { assetResourceId } from "@/lib/canvas/canvas-resource-id";
import { ApiError } from "@/services/api/request";
import { getWorkspaceAsset } from "@/services/api/workspace-data";
import { listWorkspaceAssetsPage } from "@/services/api/workspace-assets";
import { useAssetStore, type Asset } from "@/stores/use-asset-store";

export type CompositeAssetIdentity = { canvasId: string; nodeId: string; taskId: string; rawAssetId?: string; signal?: AbortSignal };

export function isCanvasCompositeAsset(asset: Asset, input: CompositeAssetIdentity) {
    const resourceId = assetResourceId(asset);
    return asset.kind === "image" && asset.metadata?.source === "canvas-generation" &&
        asset.metadata.canvasId === input.canvasId && asset.metadata.nodeId === input.nodeId &&
        asset.metadata.taskId === input.taskId && Boolean(resourceId) && asset.id !== input.rawAssetId;
}

// A previous delivery may have saved its composite before the canvas commit was
// paused. Reuse that persisted product even after a reload lost the local card.
export async function findSavedCanvasComposite(input: CompositeAssetIdentity, dependencies = {
    assets: () => useAssetStore.getState().assets,
    getAsset: getWorkspaceAsset,
    listPage: listWorkspaceAssetsPage,
}): Promise<Asset | undefined> {
    for (const candidate of dependencies.assets().filter(asset => isCanvasCompositeAsset(asset, input))) {
        try {
            const { asset } = await dependencies.getAsset(candidate.id, input.signal);
            if (isCanvasCompositeAsset(asset, input)) return asset;
        } catch (error) {
            if (!(error instanceof ApiError && error.status === 404)) throw error;
        }
    }
    for (let page = 1; ; page++) {
        const result = await dependencies.listPage({ page, pageSize: 100, kind: "image" }, { signal: input.signal });
        for (const value of result.assets) {
            // Unrelated historical library records must not prevent recovery.
            let asset: Asset;
            try { asset = parseAssetRecord(value); } catch { continue; }
            if (isCanvasCompositeAsset(asset, input)) return asset;
        }
        if (!result.hasMore) return undefined;
    }
}
