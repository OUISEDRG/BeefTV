import { expect, test } from "bun:test";
import { applyFrameDrop, getCollapsedParentFrame, getFrameChildren, getFrameDescendantIds, resolveFrameConnection } from "@/lib/canvas/canvas-frame";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";

function node(id: string, parentId?: string, frame = false, collapsed = false): CanvasNodeData {
    return { id, parentId, type: frame ? CanvasNodeType.Frame : CanvasNodeType.Video, title: id, position: { x: 0, y: 0 }, width: 100, height: 100,
        metadata: frame ? { frame: { collapsed, expandedWidth: 100, expandedHeight: 100 } } : {} };
}

test("recursive frame membership is order-independent and direct children keep their original contract", () => {
    const nodes = [node("video", "inner"), node("outer", undefined, true), node("other"), node("inner", "outer", true)];
    expect(getFrameChildren("outer", nodes).map(node => node.id)).toEqual(["inner"]);
    expect(getFrameDescendantIds("outer", nodes)).toEqual(new Set(["inner", "video"]));
    nodes[1].parentId = "inner";
    expect(getFrameDescendantIds("outer", nodes)).toEqual(new Set(["inner", "video"]));
});

test("nested contents and connections resolve to their outermost collapsed ancestor", () => {
    const nodes = [node("outer", undefined, true, true), node("inner", "outer", true, true), node("video", "inner"), node("second", "inner"), node("outside")];
    expect(getCollapsedParentFrame(nodes[2], nodes)?.id).toBe("outer");
    expect(resolveFrameConnection({ id: "inside", fromNodeId: "video", toNodeId: "second" }, nodes)).toBeNull();
    expect(resolveFrameConnection({ id: "outside", fromNodeId: "video", toNodeId: "outside" }, nodes)?.from.id).toBe("outer");
    nodes[0].metadata!.frame!.collapsed = false;
    expect(getCollapsedParentFrame(nodes[2], nodes)?.id).toBe("inner");
    nodes[1].metadata!.frame!.collapsed = false;
    expect(getCollapsedParentFrame(nodes[2], nodes)).toBeNull();
});

test("moving a nested frame preserves its episode parent while a media drop can still detach", () => {
    const nodes = [node("outer", undefined, true), node("inner", "outer", true), node("video", "inner")];
    expect(applyFrameDrop(nodes, new Set(["inner"]), null)[1].parentId).toBe("outer");
    expect(applyFrameDrop(nodes, new Set(["video"]), null)[2].parentId).toBeUndefined();
});
