import { describe, expect, test } from "bun:test";
import { canvasNodeFailureView } from "@/lib/canvas/canvas-node-failure-view";
import { isCanvasNodeGenerating } from "@/lib/canvas/canvas-node-task-state";
import { recoverCanvasGenerationTaskNode } from "@/pages/canvas/use-canvas-generation";
import { attachNodeEffectKey } from "@/services/generation-task-materializer";
import type { GenerationTask } from "@/services/api/task-center";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";
import type { SetStateAction } from "react";

const completed: GenerationTask = {
    id: "task-finished", projectId: "canvas", type: "canvas_video", status: "succeeded", prompt: "unchanged prompt", attempts: 1,
    progress: 100, stage: "任务完成", startedAt: "2026-11-01T05:26:38Z", completedAt: "2026-11-01T05:34:09Z",
    createdAt: "2026-11-01T05:26:37Z", updatedAt: "2026-11-01T05:34:09Z",
    outcome: { phase: "ready", reason: "result_ready", regenerate: "allowed" },
    outputs: [{ outputIndex: 0, mediaType: "video", materializedAssetId: "asset-finished" }],
};

function attachedNode(): CanvasNodeData {
    return {
        id: "video", type: CanvasNodeType.Video, title: "视频节点 2", position: { x: 12, y: 34 }, width: 240, height: 300,
        metadata: {
            status: "success", content: "resource://finished", storageKey: "resources/finished.mp4", assetId: "asset-finished",
            prompt: "unchanged prompt", taskId: completed.id, taskStatus: "succeeded", taskProgress: 100,
            taskUpdatedAt: "2026-11-01T05:34:08Z", generationSubmissionState: "accepted",
            taskOutcome: { phase: "running", reason: "task_running", regenerate: "blocked" },
            generationEffectKeys: [attachNodeEffectKey(completed.id, "video", 0)],
        },
    };
}

async function recover(node: CanvasNodeData, task = completed, current = node) {
    const nodesRef = { current: [current] };
    let consumeCalls = 0;
    await recoverCanvasGenerationTaskNode({
        projectId: "canvas", node, completed: task, continuationOnly: false, nodesRef,
        setNodes: action => { nodesRef.current = typeof action === "function" ? action(nodesRef.current) : action; },
        applyGenerationTaskResult: async () => { consumeCalls++; }, signal: new AbortController().signal,
    });
    return { node: nodesRef.current[0]!, consumeCalls };
}

describe("completed canvas result state reconciliation", () => {
    test("an attached successful video with a stale running outcome is selected for task reconciliation", () => {
        const node = attachedNode();
        expect(isCanvasNodeGenerating(node, null)).toBe(true);
        expect(canvasNodeFailureView(node).needsRecovery).toBe(true);
        expect(canvasNodeFailureView({ ...node, metadata: { ...node.metadata, taskOutcome: { phase: "fetching", reason: "result_fetching", regenerate: "blocked" } } }).needsRecovery).toBe(true);
    });

    test("a confirmed ready task refreshes its attached video without consuming or binding again", async () => {
        const original = attachedNode();
        const { node, consumeCalls } = await recover(original);
        expect(consumeCalls).toBe(0);
        expect(node.metadata?.taskOutcome).toEqual(completed.outcome);
        expect(node.metadata?.taskCompletedAt).toBe(completed.completedAt);
        expect(node.metadata?.taskUpdatedAt).toBe(completed.updatedAt);
        expect(node.metadata?.taskStage).toBe(completed.stage);
        expect(isCanvasNodeGenerating(node, null)).toBe(false);
        expect(canvasNodeFailureView(node).needsRecovery).toBe(false);
        expect(node.metadata?.content).toBe(original.metadata?.content);
        expect(node.metadata?.prompt).toBe(original.metadata?.prompt);
        expect(node.position).toEqual(original.position);
    });

    test("late completion for the old task cannot touch a newer task on the same node", async () => {
        const original = attachedNode();
        const newer = { ...original, metadata: { ...original.metadata, taskId: "task-new", taskStatus: "running" as const, status: "loading" as const } };
        const result = await recover(original, completed, newer);
        expect(result.consumeCalls).toBe(0);
        expect(result.node).toEqual(newer);
        expect(isCanvasNodeGenerating(result.node)).toBe(true);
    });

    test("a queued React state update cannot overwrite a newer task", async () => {
        const node = attachedNode();
        const updates: SetStateAction<CanvasNodeData[]>[] = [];
        await recoverCanvasGenerationTaskNode({
            projectId: "canvas", node, completed, continuationOnly: false, nodesRef: { current: [node] },
            setNodes: action => { updates.push(action); }, applyGenerationTaskResult: async () => { throw new Error("unexpected bind"); },
            signal: new AbortController().signal,
        });
        let current = [{ ...node, metadata: { ...node.metadata, taskId: "task-new", status: "loading" as const, taskStatus: "running" as const } }];
        const original = current;
        for (const action of updates) current = typeof action === "function" ? action(current) : action;
        expect(current).toBe(original);
        expect(isCanvasNodeGenerating(current[0])).toBe(true);
    });

    test("an aborted reconciliation neither changes the result nor consumes it", async () => {
        const node = attachedNode();
        const nodesRef = { current: [node] };
        let writes = 0, consumes = 0;
        await recoverCanvasGenerationTaskNode({
            projectId: "canvas", node, completed, continuationOnly: false, nodesRef, setNodes: () => { writes++; },
            applyGenerationTaskResult: async () => { consumes++; }, signal: AbortSignal.abort(),
        });
        expect(writes).toBe(0);
        expect(consumes).toBe(0);
        expect(nodesRef.current[0]).toBe(node);
    });

    test("old media retained for a new task still requires actual result consumption", async () => {
        const node = attachedNode();
        node.metadata = { ...node.metadata, status: "loading", taskStatus: "running", assetId: "old-asset", generationEffectKeys: [attachNodeEffectKey("old-task", node.id, 0)] };
        const result = await recover(node);
        expect(result.consumeCalls).toBe(1);
        expect(isCanvasNodeGenerating(result.node)).toBe(true);
    });

    test("missing materialized output or a non-ready authoritative outcome cannot shortcut delivery", async () => {
        for (const task of [
            { ...completed, outputs: [{ outputIndex: 0, mediaType: "video" as const }] },
            { ...completed, outcome: { phase: "retrievable" as const, regenerate: "blocked" as const, reason: "delivery_pending" } },
            { ...completed, outcome: undefined },
        ]) {
            expect((await recover(attachedNode(), task)).consumeCalls).toBe(1);
        }
    });

    test("consistent completed nodes and stale effect keys do not trigger completed-state reconciliation", () => {
        const node = attachedNode();
        expect(canvasNodeFailureView({ ...node, metadata: { ...node.metadata, taskOutcome: completed.outcome } }).needsRecovery).toBe(false);
        expect(canvasNodeFailureView({ ...node, metadata: { ...node.metadata, generationEffectKeys: [attachNodeEffectKey("old-task", node.id, 0)] } }).needsRecovery).toBe(false);
    });
});
