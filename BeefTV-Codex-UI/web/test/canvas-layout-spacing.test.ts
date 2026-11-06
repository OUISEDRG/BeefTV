import { describe, expect, test } from "bun:test";
import { applyCanvasNodeLayout, canvasLayoutSpacing, layoutCanvasAuto, layoutCanvasInsertedNodes, layoutCanvasNodeGrid, layoutCanvasNodes, prepareCanvasLayoutNodes, spreadCanvasNodes } from "../src/lib/canvas/canvas-layout";
import { fitImageNodeToDecodedSize, fitVideoNodeToDecodedSize } from "../src/lib/canvas/canvas-node-size";
import { canRelayoutCanvasUploadBatch, fitCanvasUploadedVideoNode, readImageSourceSize } from "../src/lib/canvas/canvas-file-upload";
import { resetInterruptedGeneration } from "../src/lib/canvas/canvas-project-generation";
import { CanvasNodeType, type CanvasNodeData } from "../src/types/canvas";

const image = (id: string, width = 720, height = 360): CanvasNodeData => ({ id, type: CanvasNodeType.Image, title: id, position: { x: 100, y: 100 }, width, height, metadata: { storageKey: `resource:${id}`, status: "success", naturalWidth: width * 2, naturalHeight: height * 2 } });

for (const viewportScale of [0.2, 0.34, 0.42, 1, 2]) describe(`layout at ${viewportScale * 100}%`, () => {
    test("36 finished 2:1 assets remain four columns with usable title and connection space", () => {
        const nodes = Array.from({ length: 36 }, (_, i) => image(`image-${i}`));
        const positions = layoutCanvasAuto(nodes, [], { viewportScale });
        expect(new Set([...positions.values()].map(p => p.x)).size).toBe(4);
        expect(new Set([...positions.values()].map(p => p.y)).size).toBe(9);
        const dx = positions.get("image-1")!.x - positions.get("image-0")!.x - 720;
        const dy = positions.get("image-4")!.y - positions.get("image-0")!.y - 360;
        expect(dx).toBeGreaterThanOrEqual(80);
        expect(dy).toBeGreaterThanOrEqual(140);
        expect(dx * viewportScale).toBeGreaterThanOrEqual(24 - 1e-9);
        expect(dy * viewportScale).toBeGreaterThanOrEqual(48 - 1e-9);
        expect(nodes.every(n => n.width === 720 && n.height === 360 && n.position.x === 100)).toBe(true);
    });
    test("small cards also leave room for selected titles wider than their bodies", () => {
        const nodes = [image("a", 120, 60), image("b", 120, 60)];
        const positions = layoutCanvasNodes(nodes, "row", { viewportScale });
        expect((positions.get("b")!.x - positions.get("a")!.x) * viewportScale).toBeGreaterThanOrEqual(160 + 24 - 1e-9);
    });
});

test("mixed image sizes use per-column widths and per-row heights after sizing", () => {
    const nodes = [image("wide", 960, 240), image("portrait", 420, 840), image("square", 520, 520), image("last", 720, 360)];
    const positions = layoutCanvasNodeGrid(nodes, { x: 0, y: 0 }, 2);
    expect(positions.get("portrait")!.x).toBe(960 + 80);
    expect(positions.get("square")!.y).toBe(840 + 140);
    expect(positions.get("last")!.x).toBe(positions.get("portrait")!.x);
});

test("uploaded batches keep actual wide-image dimensions and never use a fixed center stride", () => {
    const nodes = [image("a", 900, 450), image("b", 720, 360), image("c", 420, 840), image("d", 520, 520)];
    const placed = layoutCanvasInsertedNodes(nodes, { x: 0, y: 0 }, [], { columns: 3, viewportScale: 0.42 });
    expect(placed[1].position.x - placed[0].position.x).toBeGreaterThanOrEqual(900 + 80);
    expect(placed[3].position.y - placed[0].position.y).toBeGreaterThanOrEqual(840 + 140);
    expect(placed.map(n => [n.width, n.height])).toEqual(nodes.map(n => [n.width, n.height]));
});

test("a completed layout moves as a block around existing content without changing that content", () => {
    const moving = [image("a"), image("b")];
    const obstacle = { ...image("locked", 1600, 800), metadata: { locked: true } };
    const all = [...moving, obstacle];
    const planned = layoutCanvasNodes(moving, "row");
    const placed = applyCanvasNodeLayout(all, moving, planned);
    expect(placed[2]).toBe(obstacle);
    expect(placed[1].position.x - placed[0].position.x).toBe(800);
    expect(placed.slice(0, 2).every(n => n.position.y >= obstacle.position.y + obstacle.height + 140 || n.position.x >= obstacle.position.x + obstacle.width + 80)).toBe(true);
});

test("a parent frame does not push its own selected children outside the group", () => {
    const children = [image("a"), image("b")].map(n => ({ ...n, parentId: "group" }));
    const frame: CanvasNodeData = { ...image("group", 2000, 2000), type: CanvasNodeType.Frame };
    const planned = layoutCanvasNodes(children, "row");
    const placed = applyCanvasNodeLayout([frame, ...children], children, planned);
    expect(placed[1].position).toEqual(planned.get("a")!);
    expect(placed[2].position).toEqual(planned.get("b")!);
});

test("spread uses distinct horizontal and title-aware vertical clearances", () => {
    const nodes = [image("a"), { ...image("b"), position: { x: 100, y: 465 } }];
    const placed = spreadCanvasNodes(nodes, { scale: 1, viewportScale: 0.2 });
    expect(placed.get("b")!.y - placed.get("a")!.y - 360).toBeGreaterThanOrEqual(canvasLayoutSpacing(0.2).vertical);
});

test("saved image aspect repair stays inside its old footprint and survives reopen", () => {
    const old = { ...image("saved", 360, 240), metadata: { storageKey: "resource:ready", status: "success" as const, generationType: "edit" as const, size: "2:1", naturalWidth: 2000, naturalHeight: 1000 } };
    const decoded = fitImageNodeToDecodedSize(old, 2000, 1000);
    expect([decoded.width, decoded.height]).toEqual([360, 180]);
    expect(decoded.position).toEqual(old.position);
    const [reopened] = resetInterruptedGeneration([decoded]);
    expect([reopened.width, reopened.height]).toEqual([360, 180]);
    expect(prepareCanvasLayoutNodes([old])[0].width).toBe(360);
});

test("generation-in-progress retains its existing fitting behavior", () => {
    const loading = { ...image("loading", 360, 240), metadata: { content: "old.png", status: "loading" as const } };
    const decoded = fitImageNodeToDecodedSize(loading, 2000, 1000);
    expect([decoded.width, decoded.height]).toEqual([720, 360]);
});

test("late decode cannot enlarge ordinary uploaded cards or override manual geometry", () => {
    const imported = { ...image("imported", 420, 420), metadata: { storageKey: "resource:imported", resultOrigin: "library" as const } };
    expect(fitImageNodeToDecodedSize(imported, 4000, 2000)).toMatchObject({ width: 420, height: 210, position: imported.position });
    const manual = { ...imported, metadata: { ...imported.metadata, manualSize: true } };
    expect(fitImageNodeToDecodedSize(manual, 4000, 2000)).toMatchObject({ width: 420, height: 420 });
});

for (const [naturalWidth, naturalHeight, width, height] of [[1080, 1920, 227.8125, 405], [1920, 1080, 420, 236.25]]) {
    test(`late ${naturalWidth}x${naturalHeight} video decode stays inside its saved bounds before layout`, () => {
        const saved = { ...image("video", naturalWidth < naturalHeight ? 720 : 420, naturalWidth < naturalHeight ? 405 : 746), type: CanvasNodeType.Video,
            metadata: { storageKey: "resource:video", status: "success" as const, naturalWidth, naturalHeight } };
        const decoded = fitVideoNodeToDecodedSize(saved, naturalWidth, naturalHeight);
        expect([decoded.width, decoded.height, decoded.position]).toEqual([width, height, saved.position]);
        expect(decoded.width).toBeLessThanOrEqual(saved.width);
        expect(decoded.height).toBeLessThanOrEqual(saved.height);
        expect(fitVideoNodeToDecodedSize(decoded, naturalWidth, naturalHeight)).toBe(decoded);
        expect(prepareCanvasLayoutNodes([saved])[0]).toEqual(decoded);
        expect(resetInterruptedGeneration([decoded])[0]).toEqual(decoded);
    });
}

test("loading and generated batch videos retain their existing decode fitting rules", () => {
    for (const metadata of [{ status: "loading" as const }, { isBatchRoot: true }, { batchRootId: "root" }]) {
        const node = { ...image("video", 720, 405), type: CanvasNodeType.Video, metadata: { storageKey: "resource:video", ...metadata } };
        const decoded = fitVideoNodeToDecodedSize(node, 1080, 1920);
        expect(decoded.width).toBe(420);
        expect(decoded.height).toBeCloseTo(420 * 16 / 9);
    }
});

test("slow video upload never replaces user geometry or ownership flags", () => {
    const before = { ...image("video", 720, 405), type: CanvasNodeType.Video, metadata: { fileUpload: "uploading" as const } };
    const automatic = fitCanvasUploadedVideoNode(before, before, 1080, 1920);
    expect(automatic.width).toBe(420);
    expect(automatic.height).toBeCloseTo(420 * 16 / 9);
    expect(automatic.position.x + automatic.width / 2).toBe(before.position.x + before.width / 2);
    expect(automatic.position.y + automatic.height / 2).toBeCloseTo(before.position.y + before.height / 2);
    for (const changed of [
        { ...before, position: { x: 101, y: 100 } },
        { ...before, width: 600 },
        { ...before, height: 600 },
        ...[{ manualSize: true }, { freeResize: true }, { locked: true }, { workflowSlot: { slotId: "slot" } }].map(metadata => ({ ...before, metadata } as CanvasNodeData)),
    ]) expect(fitCanvasUploadedVideoNode(changed, before, 1080, 1920)).toBe(changed);
    expect(fitCanvasUploadedVideoNode(before, before, 0, 0)).toBe(before);
});

test("upload completion never reflows a batch after the user drags, resizes, locks or removes a card", () => {
    const before = [image("a"), image("b")];
    expect(canRelayoutCanvasUploadBatch(before, before)).toBe(true);
    for (const changed of [
        { ...before[0], position: { x: 500, y: 100 } },
        { ...before[0], width: 600 },
        { ...before[0], metadata: { ...before[0].metadata, manualSize: true } },
        { ...before[0], metadata: { ...before[0].metadata, freeResize: true } },
        { ...before[0], metadata: { ...before[0].metadata, locked: true } },
    ]) expect(canRelayoutCanvasUploadBatch(before, [changed, before[1]])).toBe(false);
    expect(canRelayoutCanvasUploadBatch(before, [before[1]])).toBe(false);
});

test("server video dimensions can trigger the final layout only while its center stays untouched", () => {
    const before = { ...image("video", 720, 405), type: CanvasNodeType.Video };
    const after = { ...before, height: 520, position: { x: 100, y: 100 + (405 - 520) / 2 }, metadata: { naturalWidth: 720, naturalHeight: 520 } };
    expect(canRelayoutCanvasUploadBatch([before], [after])).toBe(true);
    expect(canRelayoutCanvasUploadBatch([before], [{ ...after, position: { x: 200, y: after.position.y } }])).toBe(false);
});

test("missing source dimensions are decoded strictly instead of substituting a 1:1 size", async () => {
    const original = globalThis.Image;
    let fail = false;
    try {
        globalThis.Image = class { src = ""; naturalWidth = 2000; naturalHeight = 1000; async decode() { if (fail) throw new Error("无法解码素材"); } } as unknown as typeof Image;
        expect(await readImageSourceSize("https://resource.test/image")).toEqual({ width: 2000, height: 1000 });
        fail = true;
        await expect(readImageSourceSize("https://resource.test/broken")).rejects.toThrow("无法解码素材");
        await expect(readImageSourceSize("")).rejects.toThrow("素材图片地址不可用");
    } finally { globalThis.Image = original; }
});
