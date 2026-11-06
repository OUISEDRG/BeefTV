import { describe, expect, test } from "bun:test";
import { resolveCanvasConnectionDropTarget } from "../src/lib/canvas/canvas-connection-drop-target";
import { sortCanvasNodesByStackOrder } from "../src/lib/canvas/canvas-node-stack-order";
import { CanvasNodeType, type CanvasNodeData, type Position } from "../src/types/canvas";

const node = (id: string, x = 300, y = 100, type = CanvasNodeType.Video): CanvasNodeData => ({ id, type, title: id, position: { x, y }, width: 720, height: 405 });
const target = node("target");
function drop(point: Position, scale = 1, nodes = [target], overrides: Partial<Parameters<typeof resolveCanvasConnectionDropTarget>[0]> = {}) {
    return resolveCanvasConnectionDropTarget({ nodes, point, scale, current: { nodeId: "source", handleType: "source" }, scriptScrollTopById: {}, getError: () => "", ...overrides });
}

for (const scale of [0.2, 0.42, 1, 2]) describe(`drop target at ${scale * 100}% zoom`, () => {
    test("accepts the center and all four card corners", () => {
        for (const [x, y] of [[660, 302.5], [300, 100], [1020, 100], [300, 505], [1020, 505]]) {
            expect(drop({ x, y }, scale).nodeId).toBe("target");
        }
    });
    test("keeps the outside halo at 24 screen pixels on every side", () => {
        for (const [x, y] of [[300 - 23 / scale, 250], [1020 + 23 / scale, 250], [660, 100 - 23 / scale], [660, 505 + 23 / scale]]) {
            expect(drop({ x, y }, scale).nodeId).toBe("target");
        }
        expect(drop({ x: 1020 + 25 / scale, y: 250 }, scale)).toEqual({ nodeId: null, isNearNode: false });
        // The halo follows distance to the rectangle, including rounded outer corners.
        expect(drop({ x: 300 - 20 / scale, y: 100 - 20 / scale }, scale).isNearNode).toBe(false);
    });
});

test("a direct card hit wins over another card's nearby halo", () => {
    const neighbor = node("neighbor", 1030);
    expect(drop({ x: 1018, y: 200 }, 1, [target, neighbor]).nodeId).toBe("target");
});

test("chooses the closest card in a gap, with visible stack order breaking ties", () => {
    const neighbor = node("neighbor", 1060);
    expect(drop({ x: 1034, y: 200 }, 1, [target, neighbor]).nodeId).toBe("target");
    expect(drop({ x: 1046, y: 200 }, 1, [target, neighbor]).nodeId).toBe("neighbor");
    expect(drop({ x: 1040, y: 200 }, 1, sortCanvasNodesByStackOrder([target, neighbor], ["target"])).nodeId).toBe("target");
});

test("topmost direct DOM hit wins even when the document order differs", () => {
    expect(drop({ x: 600, y: 200 }, 1, [target, node("behind")], { directNodeId: "target" }).nodeId).toBe("target");
});

test("keeps an invalid directly targeted card instead of connecting to a legal neighbor", () => {
    const result = drop({ x: 1018, y: 200 }, 1, [target, node("neighbor", 1030)], { getError: item => item.id === "target" ? "已配置模型均不支持参考视频" : "" });
    expect(result).toMatchObject({ nodeId: "target", isNearNode: true, error: "已配置模型均不支持参考视频" });
});

test("reverse dragging uses the same card body and halo", () => {
    expect(drop({ x: 660, y: 302.5 }, 1, [target], { current: { nodeId: "source", handleType: "target" } }).nodeId).toBe("target");
});

test("overlays cannot target the card beneath or offer creation", () => {
    expect(drop({ x: 660, y: 302.5 }, 1, [target], { blocked: true })).toEqual({ nodeId: null, isNearNode: true });
});

test("multi-port cards retain explicit handles and explain drops on the undifferentiated body", () => {
    const table = node("table", 300, 100, CanvasNodeType.BatchTable);
    expect(drop({ x: 300, y: 202 }, 1, [table])).toMatchObject({ nodeId: "table", handleId: "batch-reference:reference-1", error: undefined });
    expect(drop({ x: 660, y: 400 }, 1, [table])).toMatchObject({ nodeId: "table", isNearNode: true, error: "请连接到具体的分镜行、项目设定或参考列端口" });
});

test("hidden batch children and collapsed frame children cannot steal a visible target", () => {
    const batch = { ...node("batch"), metadata: { imageBatchExpanded: false } };
    const hidden = { ...node("hidden"), metadata: { batchRootId: "batch" } };
    expect(drop({ x: 660, y: 250 }, 1, [batch, hidden]).nodeId).toBe("batch");
    const frame = { ...node("frame", 300, 100, CanvasNodeType.Frame), metadata: { frame: { collapsed: true } } };
    const child = { ...node("child"), parentId: "frame" };
    expect(drop({ x: 660, y: 250 }, 1, [target, frame, child]).nodeId).toBe("target");
});

test("reverse input dragging accepts a collapsed group body while ignoring its hidden members", () => {
    const frame = { ...node("frame", 300, 100, CanvasNodeType.Frame), metadata: { frame: { collapsed: true } } };
    const child = { ...node("child"), parentId: "frame" };
    expect(drop({ x: 660, y: 250 }, 0.42, [frame, child], { current: { nodeId: "video", handleType: "target" }, directNodeId: "frame" })).toMatchObject({ nodeId: "frame", isNearNode: true });
    expect(drop({ x: 1020 + 23 / 0.42, y: 250 }, 0.42, [frame, child], { current: { nodeId: "video", handleType: "target" } })).toMatchObject({ nodeId: "frame", isNearNode: true });
});

test("visible cards keep precedence over their enclosing frame regardless of array order", () => {
    const frame = { ...node("frame", 280, 80, CanvasNodeType.Frame), width: 900, height: 600 };
    expect(drop({ x: 660, y: 250 }, 1, [target, frame]).nodeId).toBe("target");
    expect(drop({ x: 1150, y: 600 }, 1, [target, frame], { getError: item => item.id === "frame" ? "组不能作为引用目标" : "" })).toMatchObject({ nodeId: "frame", error: "组不能作为引用目标", isNearNode: true });
});
