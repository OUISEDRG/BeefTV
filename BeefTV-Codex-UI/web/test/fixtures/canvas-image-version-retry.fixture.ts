import { expect, mock } from "bun:test";
import { defaultModelCapabilityConfig } from "../../src/lib/model-capabilities";
import { CanvasNodeType, type CanvasNodeData } from "../../src/types/canvas";

const react = await import("react");
const configuration = await import("../../src/stores/use-config-store");
const generation = await import("../../src/lib/canvas/canvas-project-generation");
const image = defaultModelCapabilityConfig("openai-image").image!;
Object.assign(image, { maxOutputs: 1, fixedOutputs: 1, generationOutputs: [1] });
const channel = configuration.createModelChannel({ id: "single", enabled: true, baseUrl: "http://127.0.0.1:18766", apiKey: "fixture-only", interfaceType: "openai-image", models: ["image"], modelProfiles: [{ model: "image", capability: "image", protocol: "openai-image", capabilityConfig: { version: 1, image } }] });
const model = configuration.encodeChannelModel(channel.id, "image");
const config = { ...configuration.defaultConfig, channels: [channel], model, imageModel: model, size: "1:1" };
const requests: any[] = [];
const retries: string[] = [];
const errors: string[] = [];
let fail = false;
let waitForResult: Promise<void> | undefined;
let active = 0;
let peakActive = 0;
const no = () => {};

mock.module("react", () => ({ ...react, useCallback: (fn: any) => fn, useRef: (current: any) => ({ current }) }));
mock.module("antd", () => ({ App: { useApp: () => ({ message: { error: (text: string) => errors.push(text), warning: (text: string) => errors.push(text), info: no }, modal: { confirm: () => { throw new Error("Unexpected confirmation"); } } }) } }));
mock.module("@/stores/use-config-store", () => ({ ...configuration, useEffectiveConfig: () => config, useConfigStore: Object.assign((select: any) => select({ config, isAiConfigReady: () => true }), { getState: () => ({ config }) }) }));
mock.module("@/lib/canvas/canvas-project-generation", () => ({ ...generation,
    createGenerationRetryContext: async (taskId: string) => { retries.push(taskId); return { clientOperationId: `retry:${taskId}`, retryOfTaskId: taskId }; },
    runCanvasGenerationTaskToConsumer: async (input: any, callbacks: any) => {
        requests.push({ ...input, signal: undefined });
        active++;
        peakActive = Math.max(peakActive, active);
        callbacks.onSubmission?.({ taskClientOperationId: input.clientOperationId, generationSubmissionState: "submitting" });
        try {
            await waitForResult;
            if (fail) {
                callbacks.onSubmission?.({ taskClientOperationId: input.clientOperationId, generationSubmissionState: "unknown", taskStage: "submission_unknown" });
                throw new Error("fixture submission failed");
            }
            const task = { id: `task:${input.nodeId}`, status: "succeeded", taskCompletedAt: "2026-11-04T12:00:00Z", outcome: { phase: "ready", regenerate: "allowed", reason: "result_ready" } };
            callbacks.bindTask(task);
            await callbacks.consumeTask(task);
        } finally { active--; }
    },
}));
const { useCanvasGenerationRetry } = await import("../../src/pages/canvas/use-canvas-generation-retry");

function node(id: string, metadata: NonNullable<CanvasNodeData["metadata"]>): CanvasNodeData {
    return { id, type: CanvasNodeType.Image, title: id, width: 100, height: 100, position: { x: 0, y: 0 }, metadata: { model, prompt: "原始生图要求", generationType: "generation", generatedFromNodeId: "removed-source", ...metadata } };
}
function setup(successful = true) {
    requests.length = 0; retries.length = 0; errors.length = 0; fail = false; waitForResult = undefined; peakActive = 0;
    const root = node("root", { status: "error", isBatchRoot: true, imageBatchAsVersions: true, batchChildIds: ["a", "b"], count: 2, content: "history:old", storageKey: "resource:old", versionLabel: "A" });
    const a = node("a", { batchRootId: "root", mediaVersionOwnerId: "root", status: successful ? "success" : "error", ...(successful ? { content: "output:a", storageKey: "resource:a", versionLabel: "B" } : { taskClientOperationId: "operation:a" }), taskOutcome: { phase: "failed", regenerate: "allowed", reason: "not_charged" } });
    const b = node("b", { batchRootId: "root", mediaVersionOwnerId: "root", status: "error", taskClientOperationId: "operation:b", generationSubmissionState: "rejected", taskOutcome: { phase: "failed", regenerate: "allowed", reason: "not_charged" } });
    const nodesRef = { current: [root, a, b] };
    const setNodes = (value: any) => { nodesRef.current = typeof value === "function" ? value(nodesRef.current) : value; };
    const retry = useCanvasGenerationRetry({ projectId: "fixture", nodesRef, connectionsRef: { current: [] }, setNodes, setRunningNodeId: no, startGenerationRequest: () => new AbortController(), finishGenerationRequest: no,
        bindGenerationTask: (id, task) => setNodes((nodes: CanvasNodeData[]) => nodes.map(item => item.id === id ? { ...item, metadata: { ...item.metadata, taskId: task.id, taskStatus: task.status, taskOutcome: task.outcome } } : item)),
        applyGenerationTaskResult: async (id) => setNodes((nodes: CanvasNodeData[]) => nodes.map(item => item.id === id ? { ...item, metadata: { ...item.metadata, content: `output:${id}`, storageKey: `resource:${id}`, status: "success" } } : item)),
        regenerateImageBatch: async () => { throw new Error("Version retry must not recreate its batch"); }, assets: [], addedSkills: [],
    });
    return { nodesRef, retry, root, a, b };
}

{
    const run = setup();
    const completed = structuredClone(run.a);
    await run.retry(run.b);
    expect(errors).toEqual([]);
    expect(requests.map(input => [input.nodeId, input.config.count, input.clientOperationId])).toEqual([["b", "1", "operation:b"]]);
    expect(run.nodesRef.current.find(item => item.id === "a")).toEqual(completed);
    const root = run.nodesRef.current[0]!;
    expect(root.metadata?.taskId).toBeUndefined();
    expect(root.metadata?.batchChildIds).toEqual(["a", "b"]);
    expect(root.metadata?.batchFailedCount).toBe(0);
    expect(root.metadata?.status).toBe("success");
    expect(root.metadata?.content).toBe("output:b");
    const failed = { ...run.b, metadata: { ...run.b.metadata, taskId: "previous-b", taskClientOperationId: undefined } };
    run.nodesRef.current = run.nodesRef.current.map(item => item.id === "b" ? failed : item);
    await run.retry(failed);
    expect(retries).toEqual(["previous-b"]);
    expect(requests[1].nodeId).toBe("b");
    expect(requests[1].config.count).toBe("1");
    console.log("PASS: missing single-output slots reuse their identities and keep successful history");
}
{
    const run = setup(false);
    await run.retry(run.root);
    expect(errors).toEqual([]);
    expect(requests.map(input => input.nodeId)).toEqual(["a", "b"]);
    expect(peakActive).toBe(1);
    const duplicate = setup();
    let release!: () => void;
    waitForResult = new Promise<void>(resolve => { release = resolve; });
    const first = duplicate.retry(duplicate.b);
    const second = duplicate.retry(duplicate.b);
    await Promise.resolve();
    release();
    await Promise.all([first, second]);
    expect(requests).toHaveLength(1);
    expect(errors).toEqual([]);
    console.log("PASS: root retry is sequential and duplicate slot retry is suppressed");
}
{
    const run = setup(false);
    fail = true;
    await run.retry(run.b);
    const root = run.nodesRef.current[0]!;
    const b = run.nodesRef.current.find(item => item.id === "b")!;
    expect(b.metadata?.taskClientOperationId).toBe("operation:b");
    expect(b.metadata?.generationSubmissionState).toBe("unknown");
    expect(b.metadata?.status).toBe("error");
    expect(root.metadata?.status).toBe("error");
    expect(root.metadata?.batchFailedCount).toBe(2);
    expect(root.metadata?.content).toBe("history:old");
    expect(requests).toHaveLength(1);
    console.log("PASS: failed retries preserve submission identity and reconcile root failure");
}
