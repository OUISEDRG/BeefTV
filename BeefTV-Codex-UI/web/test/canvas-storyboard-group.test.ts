import { describe, expect, test } from "bun:test";
import { buildStoryboardGroupNodes, selectStoryboardGroupMembers } from "@/lib/canvas/canvas-storyboard-group";
import { freezeDreamaBinding } from "@/lib/canvas/dreama-workbench";
import { selectionToolbarTools } from "@/lib/canvas/tool-registry/definitions/selection-toolbar-tools";
import type { ToolContext } from "@/lib/canvas/tool-registry";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";
import type { DreamaCanvasBinding } from "@/types/dreama-workbench";

function media(id: string, type = CanvasNodeType.Image, parentId?: string): CanvasNodeData {
    return { id, type, title: id, parentId, position: { x: 100, y: 100 }, width: 320, height: 180,
        metadata: { content: "", storageKey: `resource:${id}`, prompt: `prompt ${id}`, assetId: `asset-${id}`, taskId: `task-${id}`, status: "success" } };
}

function frame(id: string, parentId?: string, dreamaSeriesId?: number): CanvasNodeData {
    return { id, type: CanvasNodeType.Frame, title: id, parentId, position: { x: 0, y: 0 }, width: 1000, height: 1000, metadata: { dreamaSeriesId } };
}

function group(nodes: CanvasNodeData[], ids: string[], defaultSeriesId?: number) {
    const selection = selectStoryboardGroupMembers(nodes, new Set(ids), defaultSeriesId);
    if (!selection.ok) throw new Error(selection.reason);
    return buildStoryboardGroupNodes(nodes, selection, frame("new-group"));
}

const binding: DreamaCanvasBinding = { schemaVersion: 1, projectId: 9, projectName: "项目", defaultSeriesId: 10, promptMode: "drama", aspectRatio: "16:9", revision: 1 };

describe("storyboard groups", () => {
    test.each([
        [CanvasNodeType.Image, CanvasNodeType.Image],
        [CanvasNodeType.Video, CanvasNodeType.Video],
        [CanvasNodeType.Image, CanvasNodeType.Video],
    ])("groups %s and %s with durable resources but no temporary URLs", (first, second) => {
        const a = media("a", first), b = media("b", second);
        b.position = { x: 460, y: 100 };
        b.metadata!.shotIndex = 8;
        const nodes = [a, b, media("unselected")];
        const before = structuredClone(nodes);
        const next = group(nodes, ["a", "b"]);
        expect(nodes).toEqual(before);
        expect(next.at(-1)?.title).toBe("分镜组 · 2 镜");
        expect(next[0].parentId).toBe("new-group");
        expect(next[1].parentId).toBe("new-group");
        expect(next[0].metadata).toEqual({ ...a.metadata, workflowKind: "shot", shotIndex: 1 });
        expect(next[1].metadata).toEqual({ ...b.metadata, workflowKind: "shot" });
        expect(next[0].id).toBe("a");
        expect(next[1].id).toBe("b");
        expect(next[2]).toBe(nodes[2]);
        expect(next[1].position.x).toBeGreaterThan(next[0].position.x);
    });

    test("preserves the common parent and episode inherited from a higher ancestor", () => {
        const nodes = [frame("episode", undefined, 25), frame("folder", "episode"), media("a", CanvasNodeType.Image, "folder"), media("b", CanvasNodeType.Video, "folder")];
        const next = group(nodes, ["a", "b"], binding.defaultSeriesId);
        expect(next.at(-1)?.parentId).toBe("folder");
        expect(next.at(-1)?.metadata?.dreamaSeriesId).toBe(25);
        for (const id of ["a", "b"]) expect(freezeDreamaBinding(binding, "canvas", id, next)).toEqual(freezeDreamaBinding(binding, "canvas", id, nodes));
    });

    test("preserves branch episode bindings when moving members into their nearest common ancestor", () => {
        const nodes = [frame("root"), frame("left", "root", 25), frame("right", "root", 25), media("a", CanvasNodeType.Image, "left"), media("b", CanvasNodeType.Video, "right")];
        const next = group(nodes, ["a", "b"], binding.defaultSeriesId);
        expect(next.at(-1)?.parentId).toBe("root");
        expect(next.at(-1)?.metadata?.dreamaSeriesId).toBe(25);
        for (const id of ["a", "b"]) expect(freezeDreamaBinding(binding, "canvas", id, next).seriesId).toBe(25);
    });

    test("allows explicit and default bindings only when they resolve to the same episode", () => {
        const nodes = [frame("episode", undefined, 10), media("a", CanvasNodeType.Image, "episode"), media("b", CanvasNodeType.Video)];
        const next = group(nodes, ["a", "b"], 10);
        expect(next.at(-1)?.parentId).toBeUndefined();
        expect(next.at(-1)?.metadata?.dreamaSeriesId).toBe(10);
        const rejected = selectStoryboardGroupMembers(nodes, new Set(["a", "b"]), 11);
        expect(rejected.ok).toBe(false);
        if (!rejected.ok) expect(rejected.reason).toContain("不同剧集");
    });

    test("rejects cross-episode selection without changing nodes", () => {
        const nodes = [frame("first", undefined, 1), frame("second", undefined, 2), media("a", CanvasNodeType.Video, "first"), media("b", CanvasNodeType.Video, "second")];
        const before = structuredClone(nodes);
        const selection = selectStoryboardGroupMembers(nodes, new Set(["a", "b"]));
        expect(selection.ok).toBe(false);
        if (!selection.ok) expect(selection.reason).toContain("不同剧集");
        expect(nodes).toEqual(before);
    });

    test("does not accept one usable member, locked nodes, empty media, or non-media nodes", () => {
        const a = media("a"), locked = media("locked"), empty = media("empty"), text = media("text", CanvasNodeType.Text);
        locked.metadata!.locked = true;
        empty.metadata = { content: "", storageKey: "resource:" };
        const nodes = [a, locked, empty, text];
        for (const ids of [["a"], ["a", "locked"], ["a", "empty"], ["a", "text"], ["a", "missing"]]) {
            expect(selectStoryboardGroupMembers(nodes, new Set(ids)).ok).toBe(false);
        }
    });

    test("rejects missing parents, cyclic groups and conflicting ancestor bindings", () => {
        const cases = [
            [media("a", CanvasNodeType.Image, "missing"), media("b")],
            [frame("cycle", "cycle"), media("a", CanvasNodeType.Image, "cycle"), media("b")],
            [frame("outer", undefined, 1), frame("inner", "outer", 2), media("a", CanvasNodeType.Image, "inner"), media("b")],
        ];
        for (const nodes of cases) expect(selectStoryboardGroupMembers(nodes, new Set(["a", "b"])).ok).toBe(false);
    });

    test("the toolbar uses the planner's eligibility for video, mixed, invalid and single selections", () => {
        const tool = selectionToolbarTools.find(item => item.id === "selection-create-storyboard")!;
        const nodes = [media("image"), media("video", CanvasNodeType.Video), media("video2", CanvasNodeType.Video), media("empty")];
        nodes[3].metadata = {};
        for (const ids of [["image", "video"], ["video", "video2"], ["video", "empty"], ["image"]]) {
            const selection = selectStoryboardGroupMembers(nodes, new Set(ids));
            const ctx = { selectedCount: ids.length, storyboardDisabledReason: selection.ok ? "" : selection.reason } as ToolContext;
            expect(tool.disabled?.(ctx)).toBe(!selection.ok);
        }
    });
});
