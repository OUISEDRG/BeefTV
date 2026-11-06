import { expect, test } from "bun:test";
import { findSavedCanvasComposite, isCanvasCompositeAsset } from "../src/services/canvas-composite-asset";
import { ApiError } from "../src/services/api/request";
import type { Asset, ImageAsset } from "../src/stores/use-asset-store";

const identity = { canvasId: "canvas", nodeId: "edited", taskId: "task", rawAssetId: "provider-asset" };
const composite: ImageAsset = { id: "composited-asset", kind: "image", title: "表情调整", coverUrl: "", tags: [],
    createdAt: "2026-11-05", updatedAt: "2026-11-05", metadata: { ...identity, source: "canvas-generation" },
    data: { dataUrl: "", storageKey: "resource:composited", width: 1774, height: 887, bytes: 1000, mimeType: "image/png" } };

test("composite reuse requires the exact canvas/node/task and excludes raw task output", () => {
    expect(isCanvasCompositeAsset(composite, identity)).toBe(true);
    for (const changed of [{ canvasId: "other" }, { nodeId: "other" }, { taskId: "other" }, { source: "task-output" }]) {
        expect(isCanvasCompositeAsset({ ...composite, metadata: { ...composite.metadata, ...changed } }, identity)).toBe(false);
    }
    expect(isCanvasCompositeAsset({ ...composite, id: identity.rawAssetId }, identity)).toBe(false);
    expect(isCanvasCompositeAsset({ ...composite, data: { ...composite.data, storageKey: "" } }, identity)).toBe(false);
});

test("cached composite must still exist in durable storage before it is reused", async () => {
    let validated = 0;
    const saved = await findSavedCanvasComposite(identity, {
        assets: () => [composite],
        getAsset: async (id) => { validated++; expect(id).toBe(composite.id); return { asset: composite }; },
        listPage: async () => { throw new Error("should reuse confirmed cache"); },
    });
    expect(saved).toEqual(composite);
    expect(validated).toBe(1);
});

test("recovery finds the durable composite after a stale cache, malformed history and unrelated pages", async () => {
    const pages: number[] = [];
    const saved = await findSavedCanvasComposite(identity, {
        assets: () => [{ ...composite, id: "removed" }],
        getAsset: async () => { throw new ApiError("removed", { status: 404 }); },
        listPage: async ({ page }) => {
            pages.push(page);
            return { page, pageSize: 100, total: 2, hasMore: page === 1,
                assets: page === 1 ? [{ broken: true }, { ...composite, id: identity.rawAssetId }] : [composite] };
        },
    });
    expect(saved).toEqual(composite);
    expect(pages).toEqual([1, 2]);
});

test("lookup failures and cancellation do not silently fall through to create another asset", async () => {
    for (const error of [new ApiError("unavailable", { status: 503 }), new DOMException("cancelled", "AbortError")]) {
        await expect(findSavedCanvasComposite(identity, {
            assets: () => [composite] as Asset[], getAsset: async () => { throw error; },
            listPage: async () => { throw new Error("unexpected fallback after uncertain lookup"); },
        })).rejects.toBe(error);
    }
});
