import { describe, expect, spyOn, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { App } from "antd";
import * as clipboard from "copy-to-clipboard";
import { useCanvasNodeOperations } from "@/pages/canvas/use-canvas-node-operations";
import { createCanvasPageUiStore } from "@/stores/canvas/use-canvas-page-ui-store";
import { CanvasNodeType, type CanvasConnection, type CanvasNodeData } from "@/types/canvas";

function fixture() {
    const frame = (id: string, parentId?: string): CanvasNodeData => ({ id, parentId, type: CanvasNodeType.Frame, title: id,
        position: { x: 100, y: 100 }, width: 1000, height: 900, metadata: { frame: { collapsed: false } } });
    const video = (id: string, parentId: string): CanvasNodeData => ({ id, parentId, type: CanvasNodeType.Video, title: id,
        position: { x: 180, y: 230 }, width: 320, height: 180, metadata: { storageKey: `resource:${id}`, content: "", prompt: `提示词 ${id}`, taskId: `task-${id}`, status: "success" } });
    const nodes = [frame("episode"), frame("storyboard", "episode"), video("a", "storyboard"), video("b", "storyboard"), frame("unrelated")];
    nodes[0].metadata!.dreamaSeriesId = 7;
    nodes[3].position = { x: 540, y: 230 };
    const connections: CanvasConnection[] = [{ id: "internal", fromNodeId: "a", toNodeId: "b", fromHandleId: "source", toHandleId: "target" }];
    return { nodes, connections };
}

function mountOperations(nodes: CanvasNodeData[], connections: CanvasConnection[]) {
    const nodesRef = { current: nodes }, connectionsRef = { current: connections }, selectedNodeIdsRef = { current: new Set<string>() };
    const pageUi = createCanvasPageUiStore();
    let operations!: ReturnType<typeof useCanvasNodeOperations>;
    function Harness() {
        operations = useCanvasNodeOperations({
            pageUi, projectId: "nested-copy-fixture", defaultDrawingEngine: "excalidraw", nodesRef, connectionsRef, selectedNodeIdsRef,
            getCanvasCenter: () => ({ x: 2000, y: 2000 }),
            setNodes: value => { nodesRef.current = typeof value === "function" ? value(nodesRef.current) : value; },
            setConnections: value => { connectionsRef.current = typeof value === "function" ? value(connectionsRef.current) : value; },
            onNodesDeleted: () => {},
        });
        return null;
    }
    renderToStaticMarkup(<App><Harness /></App>);
    return { operations, nodesRef, connectionsRef, pageUi };
}

function assertCopiedHierarchy(original: CanvasNodeData[], nodes: CanvasNodeData[], connections: CanvasConnection[], expectedOffset?: number) {
    const copies = nodes.filter(node => node.metadata?.copiedFromNodeId);
    expect(copies).toHaveLength(4);
    const bySource = new Map(copies.map(node => [node.metadata!.copiedFromNodeId!, node]));
    expect(bySource.has("unrelated")).toBe(false);
    expect(bySource.get("episode")!.parentId).toBeUndefined();
    expect(bySource.get("episode")!.metadata?.dreamaSeriesId).toBe(7);
    expect(bySource.get("storyboard")!.parentId).toBe(bySource.get("episode")!.id);
    for (const id of ["a", "b"]) {
        const copy = bySource.get(id)!;
        expect(copy.parentId).toBe(bySource.get("storyboard")!.id);
        expect(copy.metadata?.storageKey).toBe(`resource:${id}`);
        expect(copy.metadata?.prompt).toBe(`提示词 ${id}`);
        expect(copy.metadata?.taskId).toBeUndefined();
    }
    const offsetX = bySource.get("episode")!.position.x - original[0].position.x;
    const offsetY = bySource.get("episode")!.position.y - original[0].position.y;
    for (const node of original.filter(node => node.id !== "unrelated")) {
        expect(bySource.get(node.id)!.position).toEqual({ x: node.position.x + offsetX, y: node.position.y + offsetY });
        expect(nodes.find(item => item.id === node.id)).toEqual(node);
    }
    if (expectedOffset !== undefined) expect([offsetX, offsetY]).toEqual([expectedOffset, expectedOffset]);
    const internal = connections.find(connection => connection.fromNodeId === bySource.get("a")!.id)!;
    expect(internal).toMatchObject({ toNodeId: bySource.get("b")!.id, fromHandleId: "source", toHandleId: "target" });
    expect(internal.id).not.toBe("internal");
    expect(connections.filter(connection => connection.id === "internal")).toHaveLength(1);
}

describe("copying nested canvas frames", () => {
    test.each(["copy", "variant"] as const)("duplicate %s copies all descendants and internal connections", mode => {
        const { nodes, connections } = fixture();
        const original = structuredClone(nodes);
        const mounted = mountOperations(nodes, connections);
        mounted.operations.duplicateNode("episode", mode);
        assertCopiedHierarchy(original, mounted.nodesRef.current, mounted.connectionsRef.current, 36);
    });

    test("clipboard copy/paste includes descendants once even when both parent and child are selected", () => {
        const { nodes, connections } = fixture();
        const original = structuredClone(nodes);
        const mounted = mountOperations(nodes, connections);
        const clipboardWrite = spyOn(clipboard, "default").mockResolvedValue(true);
        try {
            mounted.operations.copyNodesToClipboard(new Set(["episode", "storyboard", "a"]));
            expect(mounted.operations.pasteCopiedNodes()).toBe(true);
            assertCopiedHierarchy(original, mounted.nodesRef.current, mounted.connectionsRef.current);
            expect(mounted.pageUi.getState().selectedNodeIds.size).toBe(1);
            const selectedId = [...mounted.pageUi.getState().selectedNodeIds][0];
            expect(mounted.nodesRef.current.find(node => node.id === selectedId)?.metadata?.copiedFromNodeId).toBe("episode");
        } finally {
            clipboardWrite.mockRestore();
        }
    });

    test("duplicating an inner frame preserves its external parent and remaps copied descendants", () => {
        const { nodes, connections } = fixture();
        const mounted = mountOperations(nodes, connections);
        mounted.operations.duplicateNode("storyboard", "copy");
        const copies = mounted.nodesRef.current.filter(node => node.metadata?.copiedFromNodeId);
        expect(copies).toHaveLength(3);
        const copiedGroup = copies.find(node => node.metadata?.copiedFromNodeId === "storyboard")!;
        expect(copiedGroup.parentId).toBe("episode");
        expect(copies.filter(node => node.type === CanvasNodeType.Video).every(node => node.parentId === copiedGroup.id)).toBe(true);
    });
});
