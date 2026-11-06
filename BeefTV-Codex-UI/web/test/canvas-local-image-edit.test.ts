import { expect, test } from "bun:test";
import { freezeImageEditSource, savedImageEditSource } from "../src/lib/canvas/canvas-local-image-edit";
import { CanvasNodeType, type CanvasNodeData } from "../src/types/canvas";
import { bindBackendCanvasGenerationResult } from "../src/services/canvas-generation-consumer";
import { attachNodeEffectKey } from "../src/services/generation-task-materializer";
import { useCanvasStore, type CanvasProject } from "../src/stores/canvas/use-canvas-store";
import type { GenerationTask } from "../src/services/api/task-center";
import type { Asset } from "../src/stores/use-asset-store";
import { CanvasBackendSubmitPausedError } from "../src/services/canvas-revision-conflict";

const source: CanvasNodeData = { id: "original", type: CanvasNodeType.Image, title: "原图", width: 100, height: 80,
    position: { x: 0, y: 0 }, metadata: { storageKey: "resource:new-version" } };

test("retry and recovery prefer the frozen original after replacing or deleting its source card", () => {
    for (const nodes of [[source], []]) {
        expect(savedImageEditSource(["resource:first-version"], source.id, nodes).storageKey).toBe("resource:first-version");
    }
    expect(savedImageEditSource(undefined, source.id, [source]).storageKey).toBe("resource:new-version");
    expect(() => savedImageEditSource([], source.id, [])).toThrow("原图已丢失");
});

test("local server references are frozen as resource keys before submission", async () => {
    const ref = { id: "old", name: "old.png", type: "image/png", dataUrl: "http://127.0.0.1:41111/api/resources/first-version/file" };
    expect((await freezeImageEditSource(ref)).storageKey).toBe("resource:first-version");
    expect(savedImageEditSource([ref.dataUrl], ref.id, []).dataUrl).toBe(ref.dataUrl);
});

test("recovering a finished local composite binds the same saved asset even while document saving is paused", async () => {
    const task = { id: "mask-task", type: "canvas_image", status: "succeeded", prompt: "edit" } as GenerationTask;
    const node: CanvasNodeData = { ...source, id: "edited", metadata: { taskId: task.id, status: "success", storageKey: "resource:composited",
        maskEdit: { sourceNodeId: source.id, useGuide: true, maskStorageKey: "resource:mask" }, assetId: "composited-asset",
        generationEffectKeys: [attachNodeEffectKey(task.id, "edited", 0)] } };
    const project = { id: "recovered-edit", nodes: [node], connections: [], chatSessions: [], directorScenes: [] } as unknown as CanvasProject;
    const previous = useCanvasStore.getState().projects;
    useCanvasStore.setState({ projects: [project] });
    const persisted: string[] = [];
    const bound: string[] = [];
    let rewrites = 0;
    const args = { canvasId: project.id, nodeId: node.id, task, nodesRef: { current: [node] },
        isCurrent: () => true, setNodes: () => { rewrites++; },
        runtime: {
            findCompositeAsset: async () => ({ id: "composited-asset" } as Asset),
            bindOutput: async (input: { compositeAssetId?: string }) => {
                bound.push(input.compositeAssetId || "raw-output");
                return { op: "canvas.task.bind", replayed: bound.length > 1,
                    result: { bindingStatus: "bound" as const, revision: 2, canvas: project } };
            },
            hydrateOutputs: async () => { throw new Error("unexpected raw asset hydration"); },
            ensureNodeAsset: async () => { throw new Error("unexpected asset creation"); },
            persistDocument: async (id: string) => { persisted.push(id); throw new CanvasBackendSubmitPausedError(); },
            adoptConfirmedProjection: async (canonical: CanvasProject) => canonical,
        } };
    try {
        await bindBackendCanvasGenerationResult(args);
        await bindBackendCanvasGenerationResult(args);
        expect(persisted).toEqual([project.id, project.id]);
        expect(bound).toEqual(["composited-asset", "composited-asset"]);
        expect(rewrites).toBe(2);
        args.nodesRef.current = [{ ...node, metadata: { ...node.metadata, taskId: "new-task" } }];
        await expect(bindBackendCanvasGenerationResult(args)).rejects.toMatchObject({ name: "AbortError" });
        expect(persisted).toHaveLength(2);
        expect(bound).toHaveLength(2);
        args.nodesRef.current = [node];
        args.runtime.findCompositeAsset = async () => {
            args.nodesRef.current = [{ ...node, metadata: { ...node.metadata, taskId: "new-task" } }];
            return { id: "composited-asset" } as Asset;
        };
        await expect(bindBackendCanvasGenerationResult(args)).rejects.toMatchObject({ name: "AbortError" });
        expect(bound).toHaveLength(2);
        args.nodesRef.current = [node];
        args.runtime.findCompositeAsset = async () => ({ id: "composited-asset" } as Asset);
        args.runtime.persistDocument = async () => {
            args.nodesRef.current = [];
            throw new CanvasBackendSubmitPausedError();
        };
        await expect(bindBackendCanvasGenerationResult(args)).rejects.toMatchObject({ name: "AbortError" });
        expect(bound).toHaveLength(2);
    } finally { useCanvasStore.setState({ projects: previous }); }
});
