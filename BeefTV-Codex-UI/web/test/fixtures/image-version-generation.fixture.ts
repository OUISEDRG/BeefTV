import "../helpers/localforage-bun-memory";
import { mock } from "bun:test";
import { strict as assert } from "node:assert";
import { defaultConfig } from "@/stores/use-config-store";
import { defaultImageCapabilityConfig } from "@/lib/model-capabilities";
import { useCanvasStore, type CanvasProject } from "@/stores/canvas/use-canvas-store";
import { createCanvasPageUiStore } from "@/stores/canvas/use-canvas-page-ui-store";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";
import { selectCanvasMediaVersion } from "@/lib/canvas/canvas-media-version-selection";
import { reconcileCanvasImageVersionBatches } from "@/lib/canvas/canvas-image-version-batch";
import { mediaVersionNumber } from "@/lib/canvas/canvas-media-versions";

const original = { ...await import("@/lib/canvas/canvas-project-generation") };
let requests = 0;
let active = 0;
let failIndex = 0;
let cancelController: AbortController | undefined;
const bound: string[] = [];
mock.module("@/lib/canvas/canvas-project-generation", () => ({
    ...original,
    runCanvasGenerationTaskToConsumer: async (input: any, consumer: any) => {
        assert.equal(++active, 1, "single-image jobs must be submitted sequentially");
        const index = ++requests;
        assert.equal(input.config.count, "1");
        assert.equal(input.prompt, "same prompt");
        assert.equal(input.metadata.outputNodeIds.length, 1);
        const task = { id: `task-${index}`, type: "canvas_image", status: "succeeded", completedAt: new Date(1000 * index).toISOString() };
        try {
            consumer.bindTask(task);
            if (cancelController) {
                cancelController.abort();
                throw new DOMException("Aborted", "AbortError");
            }
            if (index === failIndex) throw new Error("fixture generation failure");
            await Promise.resolve();
            await consumer.consumeTask(task);
        } finally { active--; }
    },
}));
const { executeImageGeneration } = await import("@/pages/canvas/canvas-image-generation-executor");

for (const count of [1, 4, 8]) {
    requests = 0;
    bound.length = 0;
    const owner: CanvasNodeData = { id: "owner", type: CanvasNodeType.Image, title: "Image", position: { x: 100, y: 80 }, width: 320, height: 320, metadata: { count } };
    const project = { id: "fixture", title: "Image versions", nodes: [owner], connections: [], viewport: { x: 0, y: 0, k: 1 }, chatSessions: [], directorScenes: [] } as unknown as CanvasProject;
    useCanvasStore.setState({ projects: [project] });
    const nodesRef = { current: project.nodes };
    const setNodes = (next: any) => {
        nodesRef.current = typeof next === "function" ? next(nodesRef.current) : next;
        useCanvasStore.getState().updateProject(project.id, { nodes: nodesRef.current });
    };
    const config = { ...defaultConfig, model: "single::image", imageModel: "single::image", count: String(count),
        channels: [{ id: "single", name: "Single", baseUrl: "https://example.test", models: ["image"], modelProfiles: [{ model: "image", capability: "image", protocol: "openai-image", capabilitySource: "user", capabilityConfig: { version: 1, image: { ...defaultImageCapabilityConfig(), fixedOutputs: 1, maxOutputs: 1, generationOutputs: [1] } } }] }],
    };
    let connections: any[] = [];
    const input: any = {
        nodeId: owner.id, sourceNode: owner, canvasNodes: project.nodes, canvasConnections: [], projectId: project.id,
        prompt: "same prompt", effectivePrompt: "same prompt", generationConfig: config,
        generationContext: { referenceImages: [] }, controller: new AbortController(), nodesRef, setNodes,
        setConnections: (next: any) => { connections = typeof next === "function" ? next(connections) : next; },
        pageUi: createCanvasPageUiStore(), registerPendingNodeIds() {}, startGenerationRequest() {}, finishGenerationRequest() {}, styleMetadata: {}, skillMetadata: {}, showError() {},
        bindGenerationTask: (id: string, task: any) => {
            bound.push(id);
            setNodes((nodes: CanvasNodeData[]) => nodes.map(node => node.id === id ? { ...node, metadata: { ...node.metadata, taskId: task.id } } : node));
        },
        applyGenerationTaskResult: async (id: string, task: any) => {
            setNodes((nodes: CanvasNodeData[]) => nodes.map(node => node.id === id ? { ...node, metadata: { ...node.metadata, status: "success", content: `image:${task.id}`, storageKey: `resource:${task.id}`, taskCompletedAt: task.completedAt } } : node));
        },
    };
    await executeImageGeneration(input);
    assert.equal(requests, count);
    assert.equal(nodesRef.current.filter(node => !node.metadata?.mediaVersionOwnerId).length, 1);
    assert.equal(connections.length, 0);
    assert.deepEqual(nodesRef.current[0].position, owner.position);
    if (count === 1) continue;
    assert.ok(!bound.includes(owner.id));
    const slots = nodesRef.current.filter(node => node.metadata?.mediaVersionOwnerId === owner.id);
    assert.equal(slots.length, count);
    assert.equal(new Set(slots.map(node => node.metadata?.taskId)).size, count);
    assert.equal(new Set(slots.map(node => node.metadata?.versionLabel)).size, count);
    assert.equal(nodesRef.current[0].metadata?.content, `image:task-${count}`);
    const identities = slots.map(node => [node.id, node.metadata?.taskId, node.metadata?.content]);
    setNodes(selectCanvasMediaVersion(nodesRef.current, slots[0].id));
    assert.equal(nodesRef.current[0].metadata?.count, count);
    assert.deepEqual(nodesRef.current.filter(node => node.metadata?.mediaVersionOwnerId).map(node => [node.id, node.metadata?.taskId, node.metadata?.content]), identities);
    setNodes(reconcileCanvasImageVersionBatches(JSON.parse(JSON.stringify(nodesRef.current))));
    assert.equal(nodesRef.current[0].metadata?.content, "image:task-1", "reload must retain deliberate history selection");
    const before = nodesRef.current;
    failIndex = count + 2;
    await executeImageGeneration({ ...input, sourceNode: before[0], canvasNodes: before, generationConfig: { ...config, count: "3" }, controller: new AbortController() });
    failIndex = 0;
    assert.equal(requests, count + 3);
    assert.equal(nodesRef.current.filter(node => !node.metadata?.mediaVersionOwnerId).length, 1);
    assert.equal(nodesRef.current[0].metadata?.batchFailedCount, 1);
    assert.equal(nodesRef.current[0].metadata?.content, `image:task-${count + 3}`);
    assert.equal(nodesRef.current.filter(node => node.metadata?.mediaVersionOwnerId && node.metadata.content).length, count + 2);
    assert.equal(mediaVersionNumber(nodesRef.current[0].metadata?.versionLabel), count + 2);
    for (const [id, taskId, content] of identities) {
        const slot = nodesRef.current.find(node => node.id === id)!;
        assert.equal(slot.metadata?.taskId, taskId);
        assert.equal(slot.metadata?.content, content);
    }
    if (count === 4) {
        const current = nodesRef.current;
        await executeImageGeneration({ ...input, sourceNode: current[0], canvasNodes: current, generationConfig: { ...config, count: "1" }, controller: new AbortController() });
        assert.equal(mediaVersionNumber(nodesRef.current[0].metadata?.versionLabel), count + 3);
        assert.equal(nodesRef.current.filter(node => node.metadata?.content).length, count + 3);
    } else {
        const current = nodesRef.current;
        cancelController = new AbortController();
        await executeImageGeneration({ ...input, sourceNode: current[0], canvasNodes: current, generationConfig: { ...config, count: "8" }, controller: cancelController });
        cancelController = undefined;
        assert.equal(requests, count + 4, "cancelling must not submit the remaining versions");
        assert.equal(nodesRef.current[0].metadata?.batchChildIds?.length, 1);
        const pending = nodesRef.current.find(node => node.metadata?.batchRootId === owner.id)!;
        assert.equal(pending.metadata?.taskId, `task-${count + 4}`);
        assert.equal(pending.metadata?.taskOutcome?.phase, "retrievable");
        assert.notEqual(nodesRef.current[0].metadata?.status, "loading");
    }
}
console.log("PASS: adjustable counts produce sequential durable versions, retain history, and isolate partial failure");
