import { afterEach, beforeEach, expect, test } from "bun:test";
import { apiClient } from "@/services/api/request";
import { createGenerationTaskSubscriptionService, waitForGenerationTask, type GenerationTask } from "@/services/api/task-center";
import { canvasGenerationTaskNodes } from "@/pages/canvas/canvas-generation-failure";
import { canvasNodeFailureView } from "@/lib/canvas/canvas-node-failure-view";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";

let addedWindow = false;
beforeEach(() => {
    addedWindow = typeof window === "undefined";
    if (addedWindow) Object.assign(globalThis, { window: globalThis });
});
afterEach(() => { if (addedWindow) delete (globalThis as { window?: unknown }).window; });

const pending: GenerationTask = {
    id: "test-recovery-existing-task", type: "canvas_video", status: "failed", prompt: "fixture", attempts: 1,
    error: "尚待核对", createdAt: "2026-11-01T04:52:31Z", updatedAt: "2026-11-01T04:52:31Z",
    outcome: { phase: "retrievable", regenerate: "blocked", reason: "provider_result_pending" },
};
const concluded: GenerationTask = { ...pending, error: "预检未通过", updatedAt: "2026-11-01T04:53:02Z",
    outcome: { phase: "failed", regenerate: "confirm", reason: "no_result" } };
const tick = () => new Promise<void>(resolve => setTimeout(resolve, 0));

test("a failed task awaiting recovery keeps GET polling until its authoritative no-result conclusion", async () => {
    const original = apiClient.defaults.adapter;
    const calls: unknown[] = [], updates: GenerationTask[] = [];
    apiClient.defaults.adapter = async config => {
        calls.push({ method: config.method, url: config.url });
        return { data: { code: 0, data: calls.length === 1 ? pending : concluded }, status: 200, statusText: "OK", headers: {}, config };
    };
    try {
        let failure: unknown;
        try { await waitForGenerationTask(pending.id, { intervalMs: 1, onTaskUpdate: task => updates.push(task) }); }
        catch (error) { failure = error; }
        expect(updates.map(task => task.outcome?.reason)).toEqual(["provider_result_pending", "no_result"]);
        expect((failure as { taskOutcome?: unknown }).taskOutcome).toEqual(concluded.outcome);
        expect(calls).toEqual([{ method: "get", url: `/tasks/${pending.id}` }, { method: "get", url: `/tasks/${pending.id}` }]);
    } finally { apiClient.defaults.adapter = original; }
});

test("a canvas subscription does not finish at raw failed while its outcome still requires recovery", async () => {
    let waitCalls = 0;
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    const observed: GenerationTask[] = [];
    const service = createGenerationTaskSubscriptionService({
        queryTask: async () => pending,
        waitTask: async (_id, options) => { waitCalls++; await gate; options?.onTaskUpdate?.(concluded); return concluded; },
    });
    const disconnect = service.subscribe([pending.id], task => observed.push(task));
    try {
        await tick();
        expect(waitCalls).toBe(1);
        expect(observed.at(-1)?.outcome?.phase).toBe("retrievable");
        release(); await tick();
        expect(observed.at(-1)?.outcome).toEqual(concluded.outcome);
        let nodes: CanvasNodeData[] = [{ id: "video", type: CanvasNodeType.Video, title: "视频", position: { x: 0, y: 0 }, width: 200, height: 200, metadata: { taskId: pending.id } }];
        for (const task of observed) nodes = canvasGenerationTaskNodes(nodes, "video", task);
        expect(canvasNodeFailureView(nodes[0]!).canReload).toBe(false);
        expect(canvasNodeFailureView(nodes[0]!).regenerateBlocked).toBe(false);
        expect(nodes[0]?.metadata.taskOutcome?.reason).toBe("no_result");
    } finally { release(); disconnect(); }
});

test("a new subscription waits for a fresh GET instead of settling from a cached failed snapshot", async () => {
    let reads = 0;
    const newer: GenerationTask = { ...concluded, outcome: { phase: "failed", regenerate: "allowed", reason: "not_charged" } };
    let returnFresh!: (task: GenerationTask) => void;
    const fresh = new Promise<GenerationTask>(resolve => { returnFresh = resolve; });
    const service = createGenerationTaskSubscriptionService({
        queryTask: async () => ++reads === 1 ? concluded : fresh,
        waitTask: async () => { throw new Error("terminal task must not poll"); },
    });
    const first = service.subscribe([concluded.id], () => {});
    await tick(); first();
    const seen: GenerationTask[] = [];
    const second = service.subscribe([concluded.id], task => seen.push(task));
    try {
        expect(seen).toEqual([]);
        expect(reads).toBe(2);
        returnFresh(newer); await tick();
        expect(seen).toEqual([newer]);
    } finally { returnFresh(newer); second(); }
});

test("a subscription created during a terminal notification never receives the superseded verdict", async () => {
    let reads = 0;
    let resolveFresh!: (task: GenerationTask) => void;
    const fresh = new Promise<GenerationTask>(resolve => { resolveFresh = resolve; });
    const seen: GenerationTask[] = [];
    let disconnectSecond = () => {};
    const service = createGenerationTaskSubscriptionService({
        queryTask: async () => ++reads === 1 ? concluded : fresh,
        waitTask: async () => { throw new Error("terminal task must not poll"); },
    });
    const disconnectFirst = service.subscribe([concluded.id], () => {
        disconnectSecond = service.subscribe([concluded.id], task => seen.push(task));
    });
    try {
        await tick();
        expect(seen).toEqual([]);
        expect(reads).toBe(2);
    } finally { disconnectFirst(); disconnectSecond(); resolveFresh(concluded); await tick(); }
});

test("aborting a pending recovery observation stops reads without retry or recover writes", async () => {
    const original = apiClient.defaults.adapter;
    const controller = new AbortController();
    const calls: string[] = [];
    apiClient.defaults.adapter = async config => {
        calls.push(config.method!);
        return { data: { code: 0, data: pending }, status: 200, statusText: "OK", headers: {}, config };
    };
    try {
        await expect(waitForGenerationTask(pending.id, { intervalMs: 1, signal: controller.signal, onTaskUpdate: () => controller.abort() })).rejects.toMatchObject({ name: "AbortError" });
        expect(calls).toEqual(["get"]);
    } finally { apiClient.defaults.adapter = original; }
});
