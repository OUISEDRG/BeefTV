import { expect, mock } from "bun:test";
import { createCanvasPageUiStore } from "../../src/stores/canvas/use-canvas-page-ui-store";
import { useCanvasStore } from "../../src/stores/canvas/use-canvas-store";
import { CanvasNodeType, type CanvasNodeData, type CanvasConnection } from "../../src/types/canvas";
import { defaultModelCapabilityConfig } from "../../src/lib/model-capabilities";
import { selectCanvasMediaVersion } from "../../src/lib/canvas/canvas-media-version-selection";
const react = await import("react");
const configuration = await import("../../src/stores/use-config-store");
const generation = await import("../../src/lib/canvas/canvas-project-generation");
const tasks = await import("../../src/services/api/task-center");
const image = defaultModelCapabilityConfig("dreama-images-async").image!;
image.references.maxImages = 4;
image.count = { ...image.count, max: 4 };
const channel = configuration.createModelChannel({ id: "dreama", enabled: true, baseUrl: "http://127.0.0.1:18765", apiKey: "fixture-only", interfaceType: "dreama-images-async", models: ["image"], modelProfiles: [{ model: "image", capability: "image", protocol: "dreama-images-async", imageCapabilitiesVersion: "v1", capabilityConfig: { version: 1, image } }] });
const model = configuration.encodeChannelModel(channel.id, "image");
const config = { ...configuration.defaultConfig, channels: [channel], model, imageModel: model, size: "1:1" };
const errors: string[] = [];
const requests: any[] = [];
const calls: string[] = [];
const records = new Map<string, any>();
const no = () => {};
mock.module("react", () => ({ ...react, useCallback: (fn: any) => fn, useRef: (current: any) => ({ current }) }));
mock.module("antd", () => ({ App: { useApp: () => ({ message: { error: (text: string) => errors.push(text), warning: (text: string) => errors.push(text), info: no, loading: no, destroy: no }, modal: { confirm: () => { throw new Error("Unexpected confirmation"); } } }) } }));
mock.module("@/stores/use-config-store", () => ({ ...configuration, useEffectiveConfig: () => config, useConfigStore: Object.assign((select: any) => select({ config, isAiConfigReady: () => true }), { getState: () => ({ config }) }) }));
mock.module("@/components/generation-reference-links", () => ({ createReferenceLinkResolver: () => undefined }));
mock.module("@/lib/canvas/canvas-project-generation", () => ({ ...generation, runCanvasGenerationTaskToConsumer: async (input: any, callbacks: any) => {
    requests.push(structuredClone({ ...input, signal: undefined, resolveReferenceLinks: undefined }));
    const task = { id: `task-${requests.length}`, type: "canvas_image", projectId: input.projectId, status: "succeeded", inputJson: JSON.stringify(input), outcome: { phase: "ready", regenerate: "allowed", reason: "result_ready" }, prompt: input.prompt };
    records.set(task.id, task);
    callbacks.bindTask(task);
    await callbacks.consumeTask(task);
} }));
mock.module("@/services/api/task-center", () => ({ ...tasks,
    queryGenerationTask: async (id: string) => { calls.push(`query:${id}`); return records.get(id); },
    findGenerationTaskByOperation: async (id: string) => { calls.push(`find:${id}`); return [...records.values()].find(task => task.clientOperationId === id); },
    retryGenerationTask: async (id: string) => { calls.push(`retry:${id}`); const task = { ...records.get(id), id: `retry-${id}`, status: "succeeded", outcome: { phase: "ready", regenerate: "allowed", reason: "result_ready" } }; records.set(task.id, task); return task; },
    recoverTask: async (id: string) => { calls.push(`recover:${id}`); const task = { ...records.get(id), status: "succeeded", outcome: { phase: "ready", regenerate: "allowed", reason: "result_ready" } }; records.set(id, task); return task; },
    waitForGenerationTask: async (id: string) => { calls.push(`wait:${id}`); return { ...records.get(id), status: "succeeded" }; },
}));
const { useCanvasGenerationExecutor } = await import("../../src/pages/canvas/use-canvas-generation-executor");
const { useCanvasGenerationRetry } = await import("../../src/pages/canvas/use-canvas-generation-retry");
const { createImageToolGenerationNode } = await import("../../src/lib/canvas/image-tool-generation");

function setup(native = false) {
    errors.length = 0; requests.length = 0; calls.length = 0; records.clear();
    channel.modelProfiles![0]!.capabilityConfig!.image!.fixedOutputs = native ? 2 : undefined;
    const source: CanvasNodeData = { id: "original", type: CanvasNodeType.Image, title: "人物", parentId: "episode", position: { x: 0, y: 0 }, width: 100, height: 100, metadata: { model, storageKey: "resource:original", content: "/api/resources/original/file" } };
    const root = createImageToolGenerationNode({ source, id: "root", title: "视角", kind: "angle", instruction: "转向左侧", config });
    root.metadata!.count = 2;
    const nodesRef = { current: [source, root, { ...source, id: "episode", type: CanvasNodeType.Frame, parentId: undefined, metadata: { dreamaSeriesId: 22 } }] as CanvasNodeData[] };
    const connectionsRef = { current: [{ id: "ref", fromNodeId: source.id, toNodeId: root.id }] as CanvasConnection[] };
    const projectId = useCanvasStore.getState().importProject({ title: "retry", nodes: nodesRef.current, connections: connectionsRef.current, integrations: { dreama: { schemaVersion: 1, projectId: 41, projectName: "项目", promptMode: "drama", aspectRatio: "1:1", defaultSeriesId: 11, revision: 1 } } });
    const setNodes = (value: any) => { nodesRef.current = typeof value === "function" ? value(nodesRef.current) : value; };
    const setConnections = (value: any) => { connectionsRef.current = typeof value === "function" ? value(connectionsRef.current) : value; };
    const bindGenerationTask = (id: string, task: any) => setNodes((nodes: CanvasNodeData[]) => nodes.map(node => node.id === id ? { ...node, metadata: { ...node.metadata, taskId: task.id, taskStatus: task.status, taskOutcome: task.outcome, generationSubmissionState: "accepted" } } : node));
    const applyGenerationTaskResult = async (id: string, task: any) => setNodes((nodes: CanvasNodeData[]) => nodes.map(node => node.id === id ? { ...node, metadata: { ...node.metadata, content: `output:${task.id}:${id}`, storageKey: `resource:${id}`, status: "success" } } : node));
    const common = { projectId, pageUi: createCanvasPageUiStore(), nodesRef, connectionsRef, setNodes, setConnections, setRunningNodeId: no, startGenerationRequest: () => new AbortController(), finishGenerationRequest: no, bindGenerationTask, applyGenerationTaskResult, assets: [], addedSkills: [] };
    const generate = useCanvasGenerationExecutor(common);
    const retry = useCanvasGenerationRetry({ ...common, regenerateImageBatch: async (id, prompt, options) => { await generate(id, "image", prompt, options); } });
    const child = () => nodesRef.current.filter(node => node.metadata?.batchRootId === "root");
    const fail = (id: string, patch: any = {}) => { setNodes((nodes: CanvasNodeData[]) => nodes.map(node => node.id === id ? { ...node, parentId: undefined, metadata: { ...node.metadata, content: undefined, storageKey: undefined, status: "error", taskStatus: "failed", taskOutcome: { phase: "failed", regenerate: "allowed", reason: "not_charged" }, ...patch } } : node)); return nodesRef.current.find(node => node.id === id)!; };
    return { root, nodesRef, connectionsRef, projectId, generate, retry, child, fail };
}

{
    const run = setup();
    await run.generate("root", "image", run.root.metadata!.composerContent!);
    expect(errors).toEqual([]);
    expect(run.child()).toHaveLength(2);
    expect(run.child().map(node => node.parentId)).toEqual(["episode", "episode"]);
    expect(requests).toHaveLength(2);
    const completed = structuredClone(run.child()[0]);
    const failed = run.fail(run.child()[1]!.id, { taskId: undefined, generationSubmissionState: "rejected", taskClientOperationId: undefined, generationErrorCode: "request_not_sent" });
    await run.retry(failed);
    expect(errors).toEqual([]);
    expect(requests).toHaveLength(3);
    expect(requests[2].config.count).toBe("1");
    expect(requests[2].referenceImages.map((ref: any) => ref.id)).toEqual(["original"]);
    expect(requests[2].metadata.providerOptions.dreama.seriesId).toBe(22);
    expect(run.nodesRef.current.find(node => node.id === failed.id)?.parentId).toBe("episode");
    expect(run.child()[0]).toEqual(completed);
    expect(run.nodesRef.current.filter(node => node.metadata?.batchRootId === "root")).toHaveLength(2);
    const pending = run.fail(failed.id);
    const pendingTaskId = pending.metadata!.taskId!;
    records.set(pendingTaskId, { ...records.get(pendingTaskId), status: "running", outcome: { phase: "running", regenerate: "blocked", reason: "task_running" } });
    calls.length = 0;
    await run.retry(pending);
    expect(calls).toEqual([`query:${pendingTaskId}`, `wait:${pendingTaskId}`]);
    expect(requests).toHaveLength(3);
    const unknown = run.fail(failed.id, { taskClientOperationId: "known-op", taskId: undefined, generationSubmissionState: "unknown" });
    records.set("unknown", { ...records.get(pendingTaskId), id: "unknown", clientOperationId: "known-op", status: "failed", stage: "submission_unknown", providerRequestId: undefined, receiptRecorded: false, outcome: { phase: "retrievable", regenerate: "blocked", reason: "image_recovery_pending" } });
    calls.length = 0;
    await run.retry(unknown);
    expect(calls).toEqual(["find:known-op"]);
    expect(requests).toHaveLength(3);
    // A backend recovery-pending outcome without any receipt is still uncertain,
    // even if an old response omitted the submission_unknown stage.
    records.set("unknown", { ...records.get("unknown"), stage: undefined });
    calls.length = 0;
    await run.retry(run.nodesRef.current.find(node => node.id === failed.id)!);
    expect(calls).toEqual(["query:unknown"]);
    expect(requests).toHaveLength(3);
    console.log("PASS: actual image executor retries only the missing single output with original references and episode");
}

{
    const run = setup();
    await run.generate("root", "image", run.root.metadata!.composerContent!);
    const completed = run.child()[0]!;
    run.nodesRef.current = selectCanvasMediaVersion(run.nodesRef.current, completed.id);
    const selected = run.nodesRef.current.find(node => node.id === "root")!;
    expect(selected.metadata?.content).toBe(completed.metadata?.content);
    const nextTool = createImageToolGenerationNode({ source: selected, id: "next-tool", title: "新视角", kind: "angle", instruction: "转向右侧", config });
    expect(nextTool.parentId).toBe("episode");
    run.nodesRef.current = [...run.nodesRef.current, nextTool];
    run.connectionsRef.current = [...run.connectionsRef.current, { id: "next-ref", fromNodeId: selected.id, toNodeId: nextTool.id }];
    await run.generate(nextTool.id, "image", nextTool.metadata!.composerContent!);
    expect(errors).toEqual([]);
    expect(requests).toHaveLength(3);
    expect(requests[2].metadata.providerOptions.dreama.seriesId).toBe(22);
    expect(requests[2].referenceImages.map((ref: any) => ref.id)).toEqual([selected.id]);
    console.log("PASS: completed batch outputs retain their parent episode when used by the next tool");
}

{
    const run = setup(true);
    await run.generate("root", "image", run.root.metadata!.composerContent!);
    expect(errors).toEqual([]);
    expect(requests).toHaveLength(1);
    const first = run.fail(run.child()[0]!.id);
    const second = run.fail(run.child()[1]!.id);
    records.set("task-1", { ...records.get("task-1"), status: "failed", outcome: { phase: "failed", regenerate: "allowed", reason: "task_failed" } });
    await Promise.all([run.retry(first), run.retry(second)]);
    expect(calls.filter(call => call.startsWith("retry:"))).toEqual(["retry:task-1"]);
    expect(requests).toHaveLength(1);
    expect(run.child().every(node => node.metadata?.content)).toBe(true);
    expect(run.nodesRef.current.find(node => node.id === "root")?.metadata?.status).toBe("success");
    // A root-level retry visits the task once even though the captured target list contains both outputs.
    const nativeFirst = run.fail(run.child()[0]!.id);
    run.fail(run.child()[1]!.id);
    records.set(nativeFirst.metadata!.taskId!, { ...records.get(nativeFirst.metadata!.taskId!), status: "failed", outcome: { phase: "failed", regenerate: "allowed", reason: "task_failed" } });
    calls.length = 0;
    await run.retry(run.nodesRef.current.find(node => node.id === "root")!);
    expect(calls.filter(call => call.startsWith("retry:"))).toHaveLength(1);
    expect(run.child().every(node => node.metadata?.content)).toBe(true);
    const completed = structuredClone(run.child()[0]);
    const missing = run.fail(run.child()[1]!.id);
    records.set(missing.metadata!.taskId!, { ...records.get(missing.metadata!.taskId!), status: "failed", stage: "download_failed", receiptRecorded: true, providerRequestId: "accepted-provider-job", outcome: { phase: "retrievable", regenerate: "blocked", reason: "image_recovery_pending" } });
    calls.length = 0;
    await run.retry(missing);
    expect(calls.filter(call => call.startsWith("retry:"))).toEqual([]);
    expect(calls.some(call => call.startsWith("recover:"))).toBe(true);
    expect(run.child()[0]).toEqual(completed);
    const unknown = run.fail(run.child()[1]!.id, { taskId: undefined, taskClientOperationId: "missing-operation", generationSubmissionState: "unknown" });
    calls.length = 0;
    await run.retry(unknown);
    expect(calls).toEqual(["find:missing-operation"]);
    expect(requests).toHaveLength(1);
    expect(errors.at(-1)).toContain("未查到");
    const noIdentity = run.fail(run.child()[1]!.id, { taskId: undefined, taskClientOperationId: undefined, generationSubmissionState: "rejected", generationErrorCode: "request_not_sent" });
    await run.retry(noIdentity);
    expect(requests).toHaveLength(1);
    expect(errors.at(-1)).toContain("多输出");
    console.log("PASS: stored native tasks reconcile once, preserve completed outputs and never turn recovery into paid retries");
}

{
    const run = setup(true);
    await run.generate("root", "image", run.root.metadata!.composerContent!);
    records.set("task-1", { ...records.get("task-1"), clientOperationId: "native-op", status: "failed", outcome: { phase: "failed", regenerate: "allowed", reason: "task_failed" } });
    const first = run.fail(run.child()[0]!.id);
    const second = run.fail(run.child()[1]!.id, { taskId: undefined, taskClientOperationId: "native-op" });
    await Promise.all([run.retry(first), run.retry(second)]);
    expect(calls.filter(call => call.startsWith("retry:"))).toEqual(["retry:task-1"]);
    expect(requests).toHaveLength(1);
    expect(run.child().every(node => node.metadata?.content)).toBe(true);
    const incomplete = run.fail(run.child()[1]!.id);
    records.set(incomplete.metadata!.taskId!, { ...records.get(incomplete.metadata!.taskId!), status: "failed", outcome: { phase: "failed", regenerate: "allowed", reason: "task_failed" } });
    calls.length = 0;
    await run.retry(incomplete);
    expect(calls).toEqual([`query:${incomplete.metadata!.taskId}`]);
    expect(errors.at(-1)).toContain("已有输出");
    records.set(incomplete.metadata!.taskId!, { ...records.get(incomplete.metadata!.taskId!), outcome: undefined });
    calls.length = 0;
    await run.retry(incomplete);
    expect(calls).toEqual([`query:${incomplete.metadata!.taskId}`]);
    expect(errors.at(-1)).toContain("权威执行状态");
    console.log("PASS: task-id and operation-id aliases share one retry and partial native failures never replace completed siblings");
}
