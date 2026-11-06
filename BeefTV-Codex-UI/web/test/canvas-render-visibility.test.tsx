import { expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import { shouldRefreshCanvasVirtualization } from "@/lib/canvas/canvas-viewport-render-sync";
import { useCanvasRenderModel } from "@/pages/canvas/use-canvas-render-model";
import { CanvasNodeType, type CanvasConnection, type CanvasNodeData, type ViewportTransform } from "@/types/canvas";

type RenderOptions = { selectedNodeIds?: Set<string>; pinned?: Set<string>; viewport?: ViewportTransform; connections?: CanvasConnection[] };

function renderModel(nodes: CanvasNodeData[], { selectedNodeIds = new Set<string>(), pinned, viewport = { x: 20, y: 20, k: 0.05 }, connections = [] }: RenderOptions = {}) {
    let result = { dom: [] as string[], lod: [] as string[], lodActive: false, connections: [] as Array<{ id: string; from: string; to: string }> };
    function Probe() {
        const model = useCanvasRenderModel({
            nodes, connections, assets: [], viewport,
            viewportSize: { width: 1440, height: 900 }, mediaPerformanceMode: "auto",
            selectedNodeIds, hoveredNodeId: null, dragPreview: null,
            collapsingBatchIds: new Set(), addedSkills: [], infoNodeId: null,
            maskEditNodeId: null, annotationNodeId: null, splitNodeId: null,
            upscaleNodeId: null, angleNodeId: null, lightingNodeId: null,
            emotionNodeId: null, previewNodeId: null, contextMenu: null,
            versionCompareRootId: null, directorNodeId: null, scriptEditorNodeId: null,
            dialogNodeId: null, lodPinnedNodeIds: pinned,
        });
        result = { dom: model.visibleNodes.map(node => node.id), lod: model.lodNodes.map(node => node.id), lodActive: model.lodActive,
            connections: model.displayConnections.map(item => ({ id: item.connection.id, from: item.from.id, to: item.to.id })) };
        return null;
    }
    renderToStaticMarkup(<Probe />);
    return result;
}

/** Everything the user sees: DOM cards plus overview-canvas nodes. */
function displayedIds(nodes: CanvasNodeData[], options?: RenderOptions) {
    const { dom, lod } = renderModel(nodes, options);
    return new Set([...dom, ...lod]);
}

function nodes(count: number): CanvasNodeData[] {
    return Array.from({ length: count }, (_, i) => ({
        id: `node-${i}`, type: i % 5 ? CanvasNodeType.Image : CanvasNodeType.Video,
        title: `Media ${i}`, width: 320, height: 180,
        position: { x: i % 32 * 360, y: Math.floor(i / 32) * 240 },
    }));
}

for (const count of [80, 512]) {
    test(`overview displays every one of ${count} visible media nodes`, () => {
        const fixture = nodes(count);
        expect(displayedIds(fixture)).toEqual(new Set(fixture.map(node => node.id)));
    });
}

test("offscreen selection is retained without displacing visible nodes", () => {
    const fixture = nodes(512);
    const offscreen = { ...fixture[0], id: "selected-offscreen", position: { x: 100000, y: 100000 } };
    expect(displayedIds([...fixture, offscreen], { selectedNodeIds: new Set([offscreen.id]) }))
        .toEqual(new Set([...fixture, offscreen].map(node => node.id)));
});

test("readable zoom culls offscreen DOM cards", () => {
    const fixture = nodes(12);
    const offscreen = { ...fixture[0], id: "offscreen", position: { x: 100000, y: 100000 } };
    const model = renderModel([...fixture, offscreen], { viewport: { x: 0, y: 0, k: 1 } });
    expect(model.dom).not.toContain("offscreen");
});

test("overview canvas receives offscreen nodes so camera previews need no React refresh", () => {
    const fixture = nodes(80);
    const offscreen = { ...fixture[0], id: "offscreen", position: { x: 100000, y: 100000 } };
    const model = renderModel([...fixture, offscreen]);
    expect(model.lodActive).toBe(true);
    expect(model.lod).toContain("offscreen");
    expect(model.dom).not.toContain("offscreen");
});

test("far zoom draws cards in the overview canvas and mounts only pinned DOM cards", () => {
    const fixture = nodes(512);
    const model = renderModel(fixture, { pinned: new Set(["node-3", "node-7"]) });
    expect(model.lodActive).toBe(true);
    expect(model.lod).toHaveLength(512);
    expect(new Set(model.dom)).toEqual(new Set(["node-3", "node-7"]));
});

test("playing or editing nodes remain mounted when panned outside the overview window", () => {
    const fixture = nodes(80);
    const offscreen = { ...fixture[0], id: "playing-offscreen", position: { x: 100000, y: 100000 } };
    const model = renderModel([...fixture, offscreen], { pinned: new Set([offscreen.id]) });
    expect(model.lodActive).toBe(true);
    expect(model.dom).toContain(offscreen.id);
});

test("readable zoom keeps full DOM cards and no overview canvas", () => {
    const fixture = nodes(12);
    const model = renderModel(fixture, { viewport: { x: 0, y: 0, k: 1 } });
    expect(model.lodActive).toBe(false);
    expect(model.lod).toHaveLength(0);
    expect(model.dom.length).toBeGreaterThan(0);
});

test("frames stay DOM in the overview so their titles and controls remain", () => {
    const frame: CanvasNodeData = { id: "frame", type: CanvasNodeType.Frame, title: "Group", width: 2000, height: 1200, position: { x: 0, y: 0 } };
    const model = renderModel([frame, ...nodes(80)]);
    expect(model.lodActive).toBe(true);
    expect(model.dom).toContain("frame");
    expect(model.lod).not.toContain("frame");
});

test.each([1, 0.05])("collapsing an outer frame hides every nested member and routes connections at zoom %s", k => {
    const outer: CanvasNodeData = { id: "outer", type: CanvasNodeType.Frame, title: "Episode", width: 500, height: 400, position: { x: 20, y: 20 }, metadata: { frame: { collapsed: true, expandedWidth: 500, expandedHeight: 400 } } };
    const inner: CanvasNodeData = { ...outer, id: "inner", parentId: "outer", metadata: { frame: { collapsed: false, expandedWidth: 300, expandedHeight: 200 } } };
    const first: CanvasNodeData = { ...nodes(1)[0], id: "first", parentId: "inner" };
    const second: CanvasNodeData = { ...first, id: "second", type: CanvasNodeType.Image, metadata: { content: "image.png" } };
    const outside: CanvasNodeData = { ...first, id: "outside", parentId: undefined, position: { x: 800, y: 20 } };
    const result = renderModel([outer, inner, first, second, outside, ...nodes(80)], { viewport: { x: 0, y: 0, k }, selectedNodeIds: new Set(["first"]), pinned: new Set(["first"]),
        connections: [{ id: "internal", fromNodeId: "first", toNodeId: "second" }, { id: "external", fromNodeId: "first", toNodeId: "outside" }] });
    const displayed = new Set([...result.dom, ...result.lod]);
    expect(displayed.has("outer")).toBe(true);
    for (const id of ["inner", "first", "second"]) expect(displayed.has(id)).toBe(false);
    expect(result.connections).toEqual([{ id: "external", from: "outer", to: "outside" }]);
});

test("zooming out reuses pre-rendered margin until exceeded, then mounts newly visible cards above LOD", () => {
    const size = { width: 1440, height: 900 };
    const rendered: ViewportTransform = { x: 0, y: 0, k: 1 };
    // A small zoom-out centered on the viewport stays within the 128px pre-rendered margin.
    expect(shouldRefreshCanvasVirtualization(rendered, { x: 57.6, y: 36, k: 0.92 }, 0, 100, size, 128)).toBe(false);
    // A larger zoom-out above the LOD threshold expands past the 128px margin and triggers a refresh.
    const zoomedOut: ViewportTransform = { x: 360, y: 225, k: 0.5 };
    expect(shouldRefreshCanvasVirtualization(rendered, zoomedOut, 0, 100, size, 128)).toBe(true);

    const centerNode: CanvasNodeData = { id: "center", type: CanvasNodeType.Image, title: "Center", width: 720, height: 480, position: { x: 100, y: 100 } };
    const edgeNode: CanvasNodeData = { id: "edge", type: CanvasNodeType.Image, title: "Edge", width: 720, height: 480, position: { x: 1800, y: 100 } };
    const farNode: CanvasNodeData = { id: "far", type: CanvasNodeType.Image, title: "Far", width: 720, height: 480, position: { x: 6000, y: 100 } };
    const initial = renderModel([centerNode, edgeNode, farNode], { viewport: rendered });
    expect(initial.lodActive).toBe(false);
    expect(initial.dom).toEqual(["center"]);

    const refreshed = renderModel([centerNode, edgeNode, farNode], { viewport: zoomedOut });
    expect(refreshed.lodActive).toBe(false);
    expect(refreshed.dom).toEqual(["center", "edge"]);
});
