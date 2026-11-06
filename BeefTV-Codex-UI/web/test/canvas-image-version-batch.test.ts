import { expect, test } from "bun:test";
import { reconcileCanvasImageVersionBatches } from "../src/lib/canvas/canvas-image-version-batch";
import { isCanvasNodeGenerating } from "../src/lib/canvas/canvas-node-task-state";
import { applyGenerationTaskResultToNodes } from "../src/lib/canvas/canvas-generation-task-sync";
import { CanvasNodeType, type CanvasNodeData, type CanvasNodeMetadata } from "../src/types/canvas";
import type { GenerationTask } from "../src/services/api/task-center";
import { recoverCanvasGenerationTaskNode } from "../src/pages/canvas/use-canvas-generation";
import { bindBackendCanvasGenerationResult } from "../src/services/canvas-generation-consumer";
import { useCanvasStore, type CanvasProject } from "../src/stores/canvas/use-canvas-store";

function owner(metadata: CanvasNodeMetadata = {}): CanvasNodeData {
    return { id: "owner", type: CanvasNodeType.Image, title: "图片", position: { x: 13, y: 27 }, width: 340, height: 240,
        metadata: { imageBatchAsVersions: true, isBatchRoot: true, batchChildIds: ["one", "two"], count: 2, status: "loading", ...metadata } };
}

function slot(id: string, metadata: CanvasNodeMetadata = {}): CanvasNodeData {
    return { id, type: CanvasNodeType.Image, title: "候选图", position: { x: 13, y: 27 }, width: 340, height: 240,
        metadata: { mediaVersionOwnerId: "owner", batchRootId: "owner", versionOfNodeId: "owner", taskId: `task-${id}`, status: "loading", ...metadata } };
}

function success(id: string, completedAt: string): CanvasNodeData {
    return slot(id, { status: "success", taskStatus: "succeeded", content: `resource://${id}`, storageKey: `resource:${id}`, assetId: `asset-${id}`, naturalWidth: 1536, naturalHeight: 1024, taskCompletedAt: completedAt });
}

test("independent results stay in hidden slots while the owner projects the latest completed version", () => {
    const source = owner({ taskOutcome: { phase: "ready", regenerate: "allowed", reason: "result_ready" } });
    const first = reconcileCanvasImageVersionBatches([source, success("one", "2026-11-04T01:00:00Z"), slot("two")]);
    expect(first[0]).toMatchObject({ id: "owner", width: 340, height: 240, position: { x: 13, y: 27 }, metadata: { content: "resource://one", assetId: "asset-one", versionLabel: "A", count: 2, status: "loading" } });
    expect(first[0]?.metadata?.taskId).toBeUndefined();
    expect(first[0]?.metadata?.taskOutcome).toBeUndefined();
    expect(isCanvasNodeGenerating(first[0])).toBe(true);
    expect(first[1]?.metadata).toMatchObject({ taskId: "task-one", content: "resource://one", versionLabel: "A", mediaVersionOwnerId: "owner" });
    const finished = reconcileCanvasImageVersionBatches(first.map(node => node.id === "two" ? success("two", "2026-11-04T01:00:02Z") : node));
    expect(finished[0]?.metadata).toMatchObject({ content: "resource://two", versionLabel: "B", primaryImageId: "two", status: "success", count: 2, batchFailedCount: 0 });
    expect(finished[1]?.metadata?.taskId).toBe("task-one");
    expect(finished[2]?.metadata?.taskId).toBe("task-two");
    expect(reconcileCanvasImageVersionBatches(finished)).toBe(finished);
});

test("restored results are numbered by completion time and appended after existing history", () => {
    const previous = slot("old", { batchRootId: undefined, content: "resource://old", storageKey: "resource:old", versionLabel: "A", status: "success" });
    const nodes = [owner({ content: "resource://old", storageKey: "resource:old", versionLabel: "A" }), success("one", "2026-11-04T01:00:02Z"), success("two", "2026-11-04T01:00:01Z"), previous];
    const result = reconcileCanvasImageVersionBatches(JSON.parse(JSON.stringify(nodes)));
    expect(result.find(node => node.id === "two")?.metadata?.versionLabel).toBe("B");
    expect(result.find(node => node.id === "one")?.metadata?.versionLabel).toBe("C");
    expect(result[0]?.metadata).toMatchObject({ content: "resource://one", versionLabel: "C", status: "success" });
    expect(result.find(node => node.id === "old")?.metadata?.content).toBe("resource://old");
});

test("partial failure keeps successful versions and a selected historical version survives replay", () => {
    const failed = slot("two", { status: "error", taskStatus: "failed", errorDetails: "生成失败" });
    const result = reconcileCanvasImageVersionBatches([owner(), success("one", "2026-11-04T01:00:00Z"), failed]);
    expect(result[0]?.metadata).toMatchObject({ status: "success", batchFailedCount: 1, content: "resource://one" });
    expect(result[2]?.metadata?.versionLabel).toBeUndefined();
    const selected = result.map(node => node.id === "owner" ? { ...node, metadata: { ...node.metadata, mediaVersionSelected: true, content: "resource://chosen", storageKey: "resource:chosen", assetId: "asset-chosen", versionLabel: "Z" } } : node);
    expect(reconcileCanvasImageVersionBatches(selected)).toBe(selected);
    expect(selected[0]?.metadata?.content).toBe("resource://chosen");
});

test("all failures preserve the prior image and never become image versions", () => {
    const nodes = [owner({ content: "resource://old", storageKey: "resource:old", assetId: "asset-old", versionLabel: "A" }), slot("one", { status: "error", errorDetails: "failed one" }), slot("two", { status: "error", errorDetails: "failed two" })];
    const result = reconcileCanvasImageVersionBatches(nodes);
    expect(result[0]?.metadata).toMatchObject({ status: "error", batchFailedCount: 2, content: "resource://old", assetId: "asset-old", versionLabel: "A", errorDetails: "failed one" });
    expect(result[1]?.metadata?.versionLabel).toBeUndefined();
    expect(result[2]?.metadata?.versionLabel).toBeUndefined();
    expect(reconcileCanvasImageVersionBatches(result)).toBe(result);
});

test("unrelated nodes and ordinary historical snapshots cannot receive task replays", async () => {
    const ordinary = { ...owner(), metadata: {} };
    const historical = slot("one", { content: "resource://history", storageKey: "resource:history", batchRootId: undefined, taskId: undefined });
    const task = { id: "task-one", status: "succeeded", type: "canvas_image" } as GenerationTask;
    const nodes = [ordinary, historical];
    expect(reconcileCanvasImageVersionBatches(nodes)).toBe(nodes);
    expect((await applyGenerationTaskResultToNodes(nodes, task, "one")).updated).toBe(false);
    expect((await applyGenerationTaskResultToNodes([owner(), historical], task, "one")).updated).toBe(false);
});

test("recovery repairs a visible owner after a successful slot was already durably attached", async () => {
    const attached = success("one", "2026-11-04T01:00:00Z");
    const task = { id: "task-one", projectId: "canvas", type: "canvas_image", status: "succeeded", outcome: { phase: "ready", reason: "result_ready", regenerate: "allowed" }, outputs: [{ outputIndex: 0, mediaType: "image", materializedAssetId: "asset-one" }] } as GenerationTask;
    const nodesRef = { current: [owner({ batchChildIds: ["one"] }), attached] };
    let binds = 0;
    const recover = () => recoverCanvasGenerationTaskNode({ projectId: "canvas", node: attached, completed: task, continuationOnly: false, nodesRef,
        setNodes: action => { nodesRef.current = typeof action === "function" ? action(nodesRef.current) : action; },
        applyGenerationTaskResult: async () => { binds++; }, signal: new AbortController().signal });
    await recover();
    expect(binds).toBe(0);
    expect(nodesRef.current[0]?.metadata).toMatchObject({ content: "resource://one", assetId: "asset-one", versionLabel: "A", status: "success" });
    const versionIds = nodesRef.current.map(node => node.id);
    await recover();
    expect(nodesRef.current.map(node => node.id)).toEqual(versionIds);
    expect(nodesRef.current[1]?.metadata).toMatchObject({ taskId: "task-one", versionLabel: "A" });
});

test("the durable bind projection updates its hidden slot and visible owner together", async () => {
    const previousProjects = useCanvasStore.getState().projects;
    const nodesRef = { current: [owner({ batchChildIds: ["one"] }), slot("one")] };
    const project = { id: "canvas", title: "Canvas", revision: 1, createdAt: "2026-11-04", updatedAt: "2026-11-04", nodes: nodesRef.current, connections: [], chatSessions: [], activeChatId: null, viewport: { x: 0, y: 0, k: 1 } } as unknown as CanvasProject;
    const bound = { ...project, revision: 2, nodes: [project.nodes[0]!, success("one", "2026-11-04T01:00:00Z")] };
    const task = { id: "task-one", projectId: "canvas", type: "canvas_image", status: "succeeded", outputs: [{ outputIndex: 0, mediaType: "image", materializedAssetId: "asset-one" }] } as GenerationTask;
    useCanvasStore.setState({ projects: [project] });
    try {
        await bindBackendCanvasGenerationResult({ canvasId: "canvas", nodeId: "one", task, nodesRef, isCurrent: () => true,
            setNodes: action => { nodesRef.current = typeof action === "function" ? action(nodesRef.current) : action; },
            runtime: { hydrateOutputs: async () => undefined, persistDocument: async () => undefined,
                bindOutput: async input => ({ op: "canvas.task.bind", opId: input.operationId, replayed: false, result: { bindingStatus: "bound", canvas: bound, revision: 2 } }),
                adoptConfirmedProjection: async () => { useCanvasStore.setState({ projects: [bound] }); return bound; },
            },
        });
        expect(nodesRef.current[0]?.metadata).toMatchObject({ content: "resource://one", assetId: "asset-one", versionLabel: "A", status: "success" });
        expect(nodesRef.current[1]?.metadata).toMatchObject({ taskId: "task-one", versionLabel: "A", content: "resource://one" });
        expect(useCanvasStore.getState().projects[0]?.nodes[0]?.metadata?.content).toBe("resource://one");
    } finally {
        useCanvasStore.setState({ projects: previousProjects });
    }
});
