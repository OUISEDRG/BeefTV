import { expect, test } from "bun:test";
import { prepareInPlaceMediaVersion, mediaVersionNumber } from "../src/lib/canvas/canvas-media-versions";
import { selectCanvasMediaVersion } from "../src/lib/canvas/canvas-media-version-selection";
import { removeCanvasNodes, isHiddenBatchChild } from "../src/lib/canvas/canvas-project-domain";
import { isolateCopiedNodeMetadata } from "../src/lib/canvas/canvas-node-copy";
import { buildCanvasAgentMentionReferences, buildCanvasResourceReferences } from "../src/lib/canvas/canvas-resource-references";
import { searchCanvasNodes } from "../src/lib/canvas/canvas-node-search";
import { applyGenerationTaskResultToNodes } from "../src/lib/canvas/canvas-generation-task-sync";
import { resetGenerationTaskMetadata } from "../src/lib/canvas/canvas-project-generation";
import type { GenerationTask } from "../src/services/api/task-center";
import { CanvasNodeType, type CanvasNodeData } from "../src/types/canvas";

const source = (type = CanvasNodeType.Image): CanvasNodeData => ({ id: "owner", type, title: "画面", width: 640, height: 360, position: { x: 120, y: 90 },
    metadata: { content: "old-media", storageKey: "resource:old", assetId: "asset-old", naturalWidth: 1920, naturalHeight: 1080, status: "success", taskId: "old-task", generationEffectKeys: ["effect"], generationResultPlacement: "replace-node" } });

for (const type of [CanvasNodeType.Image, CanvasNodeType.Video, CanvasNodeType.Audio]) {
    test(`${type}: archive preserves durable media and isolates task state before replacement`, () => {
        const node = source(type);
        const nodes = prepareInPlaceMediaVersion([node], node.id);
        expect(nodes).toHaveLength(2);
        const snapshot = nodes.find(item => item.id !== node.id)!;
        expect(snapshot.metadata).toMatchObject({ content: "old-media", storageKey: "resource:old", assetId: "asset-old", versionLabel: "A", mediaVersionOwnerId: node.id });
        expect(snapshot.metadata?.taskId).toBeUndefined();
        expect(snapshot.metadata?.generationEffectKeys).toBeUndefined();
        expect(nodes[0]?.metadata?.versionLabel).toBe("B");
        expect(node.metadata?.versionLabel).toBeUndefined();
        expect(prepareInPlaceMediaVersion(nodes, node.id)).toBe(nodes);
    });
}

test("history snapshots clear provider failure details while retaining the successful media", () => {
    const node = source();
    node.metadata = { ...node.metadata, generationErrorCode: "provider_error", generationErrorSummary: "upstream failure", generationProviderCode: "upstream_busy" };
    const snapshot = prepareInPlaceMediaVersion([node], node.id)[1]!;
    expect(snapshot.metadata?.content).toBe("old-media");
    expect(snapshot.metadata?.generationErrorCode).toBeUndefined();
    expect(snapshot.metadata?.generationErrorSummary).toBeUndefined();
    expect(snapshot.metadata?.generationProviderCode).toBeUndefined();
});

test("single generation after a batch reserves a fresh label without duplicating its archived current result", () => {
    const owner = { ...source(), metadata: { ...source().metadata, versionLabel: "D", versionOfNodeId: "owner", count: 1 } };
    const slot = { ...owner, id: "task-slot", metadata: { ...owner.metadata, mediaVersionOwnerId: "owner", taskId: "original-task" } };
    const prepared = prepareInPlaceMediaVersion([owner, slot], "owner");
    expect(prepared).toHaveLength(2);
    expect(prepared[0]?.metadata?.versionLabel).toBe("E");
    expect(prepared[1]?.metadata?.versionLabel).toBe("D");
    expect(prepareInPlaceMediaVersion(prepared, "owner")).toBe(prepared);
});

test("switching history swaps both results and preserves owner identity, position and downstream references", () => {
    const nodes = prepareInPlaceMediaVersion([source()], "owner");
    const snapshotId = nodes[1]!.id;
    const completed = nodes.map(node => node.id === "owner" ? { ...node, metadata: { ...node.metadata, content: "new-media", storageKey: "resource:new", assetId: "asset-new" } } : node);
    const selected = selectCanvasMediaVersion(completed, snapshotId);
    expect(selected).toHaveLength(2);
    expect(selected[0]).toMatchObject({ id: "owner", width: 640, height: 360, position: { x: 120, y: 90 }, metadata: { content: "old-media", storageKey: "resource:old", assetId: "asset-old", versionLabel: "A", versionPrimary: true } });
    expect(selected[0]?.metadata?.mediaVersionOwnerId).toBeUndefined();
    expect(selected[1]?.metadata).toMatchObject({ content: "new-media", assetId: "asset-new", versionLabel: "B", mediaVersionOwnerId: "owner" });
    expect(selectCanvasMediaVersion(selected, snapshotId)[0]?.metadata?.content).toBe("new-media");
    const nextGeneration = prepareInPlaceMediaVersion(selected, "owner");
    expect(nextGeneration[0]?.metadata?.versionLabel).toBe("C");
});

test("failed generation can restore the old result without archiving an empty placeholder", () => {
    const nodes = prepareInPlaceMediaVersion([source()], "owner");
    nodes[0] = { ...nodes[0]!, metadata: { versionOfNodeId: "owner", versionLabel: "B", status: "error" } };
    const restored = selectCanvasMediaVersion(nodes, nodes[1]!.id);
    expect(restored).toHaveLength(1);
    expect(restored[0]?.metadata?.content).toBe("old-media");
    const running = nodes.map(node => node.id === "owner" ? { ...node, metadata: { ...node.metadata, status: "loading" as const } } : node);
    expect(selectCanvasMediaVersion(running, nodes[1]!.id)).toBe(running);
});

test("history survives serialization, stays out of canvas mentions/search and is removed with its owner", () => {
    const nodes = JSON.parse(JSON.stringify(prepareInPlaceMediaVersion([source()], "owner"))) as CanvasNodeData[];
    expect(isHiddenBatchChild(nodes[1]!, nodes)).toBe(true);
    expect(buildCanvasResourceReferences(nodes, []).map(ref => ref.nodeId)).toEqual(["owner"]);
    expect(buildCanvasAgentMentionReferences(nodes).map(ref => ref.nodeId)).toEqual(["owner"]);
    expect(searchCanvasNodes(nodes, "").map(node => node.id)).toEqual(["owner"]);
    expect(isolateCopiedNodeMetadata(nodes[1]!, new Map()).mediaVersionOwnerId).toBeUndefined();
    expect(removeCanvasNodes(nodes, new Set(["owner"])).nodes).toHaveLength(0);
});

test("more than 26 regenerations keep distinct ordered version labels", () => {
    let nodes = [source()];
    for (let i = 0; i < 30; i++) {
        nodes = prepareInPlaceMediaVersion(nodes, "owner");
        nodes = nodes.map(node => node.id === "owner" ? { ...node, metadata: { ...node.metadata, content: `media-${i}`, storageKey: `resource:${i}` } } : node);
    }
    expect(new Set(nodes.map(node => node.metadata?.versionLabel)).size).toBe(31);
    expect(mediaVersionNumber(nodes[0]?.metadata?.versionLabel)).toBe(31);
});

test("a late task result cannot replace a deliberately selected history version", async () => {
    const nodes = prepareInPlaceMediaVersion([source()], "owner");
    nodes[0] = { ...nodes[0]!, metadata: { ...nodes[0]!.metadata, content: "new-media", storageKey: "resource:new" } };
    const selected = selectCanvasMediaVersion(nodes, nodes[1]!.id);
    const task = { id: "old-task", status: "succeeded", type: "canvas_image", projectId: "canvas", clientContext: { nodeId: "owner" } } as GenerationTask;
    const replay = await applyGenerationTaskResultToNodes(selected, task, "owner");
    expect(replay.updated).toBe(false);
    expect(replay.nodes[0]?.metadata?.content).toBe("old-media");
    expect(resetGenerationTaskMetadata(selected[0]?.metadata, "loading").mediaVersionSelected).toBeUndefined();
});
