import { describe, expect, test } from "bun:test";
import { planCanvasGroupConnections, planCanvasReferenceConnections, resolveCanvasGroupReferenceSources, resolveCanvasReferenceSources } from "@/lib/canvas/canvas-group-references";
import { canvasConnectionError } from "@/lib/canvas/canvas-connection-policy";
import { defaultModelCapabilityConfig } from "@/lib/model-capabilities";
import { buildNodeMentionReferences, getCanvasReferenceReceiverId, getGenerationResourceNodes, reorderCanvasResourceConnections } from "@/lib/canvas/canvas-resource-references";
import { defaultConfig } from "@/stores/use-config-store";
import { CanvasNodeType, type CanvasConnection, type CanvasNodeData } from "@/types/canvas";

const node = (id: string, type: CanvasNodeType, patch: Partial<CanvasNodeData> = {}): CanvasNodeData => ({ id, type, title: id, position: { x: 0, y: 0 }, width: 400, height: 280, ...patch });
const image = (id: string, parentId = "group", patch: Partial<CanvasNodeData> = {}) => node(id, CanvasNodeType.Image, { parentId, metadata: { storageKey: `image:${id}` }, ...patch });
const group = (id = "group", patch: Partial<CanvasNodeData> = {}) => node(id, CanvasNodeType.Frame, patch);
const target = node("target", CanvasNodeType.Video);
const config = { ...defaultConfig };
const plan = (nodes: CanvasNodeData[], connections: CanvasConnection[] = [], extra = {}) => planCanvasGroupConnections({ sourceGroupId: "group", targetNodeId: "target", nodes, connections, config, ...extra });

describe("group source expansion", () => {
    test("recursively expands collapsed groups once and keeps shot then spatial order", () => {
        const nodes = [group("group", { metadata: { frame: { collapsed: true, expandedWidth: 600, expandedHeight: 600 } } }),
            group("nested", { parentId: "group", metadata: { frame: { collapsed: true, expandedWidth: 300, expandedHeight: 300 } } }),
            image("later-shot", "nested", { metadata: { storageKey: "image:b", shotIndex: 2 } }),
            image("bottom", "group", { position: { x: 0, y: 200 } }),
            image("right", "nested", { position: { x: 200, y: 100 } }),
            image("first-shot", "group", { position: { x: 100, y: 200 }, metadata: { storageKey: "image:a", shotIndex: 0 } }),
            image("left", "group", { position: { x: 100, y: 100 } }), target];
        const result = resolveCanvasReferenceSources(["group", "nested", "first-shot", "group"], nodes);
        expect(result.sourceNodeIds).toEqual(["first-shot", "later-shot", "left", "right", "bottom"]);
        expect(result.groupNodeIds).toEqual(["group", "nested"]);
        expect(result.skipped).toEqual([]);
        expect(resolveCanvasGroupReferenceSources("group", [...nodes].reverse()).sourceNodeIds).toEqual(result.sourceNodeIds);
        expect(plan(nodes).connections.map(edge => [edge.fromNodeId, edge.toNodeId])).toEqual(result.sourceNodeIds.map(id => [id, "target"]));
    });

    test("includes storage-only media and nonempty text, skips placeholders and structural nodes with reasons", () => {
        const nodes = [group(), image("picture"), node("sound", CanvasNodeType.Audio, { parentId: "group", metadata: { storageKey: "media:sound" } }),
            node("text", CanvasNodeType.Text, { parentId: "group", metadata: { content: "Reference text" } }),
            node("blank", CanvasNodeType.Text, { parentId: "group", metadata: { content: "   " } }),
            node("empty-image", CanvasNodeType.Image, { parentId: "group" }),
            image("loading", "group", { metadata: { storageKey: "image:old", status: "loading" } }),
            image("history", "group", { metadata: { storageKey: "image:old", mediaVersionOwnerId: "picture" } }),
            node("config", CanvasNodeType.Config, { parentId: "group" }),
            node("script", CanvasNodeType.Script, { parentId: "group" }),
            node("workflow", CanvasNodeType.Workflow, { parentId: "group" }), image("slot", "workflow")];
        const result = resolveCanvasGroupReferenceSources("group", nodes);
        expect(result.sourceNodeIds).toEqual(["picture", "sound", "text"]);
        expect(result.skipped.map(item => item.nodeId).sort()).toEqual(["blank", "config", "empty-image", "history", "loading", "script", "workflow"]);
        expect(result.skipped.every(item => item.reason.length > 0)).toBe(true);
        expect(result.sourceNodeIds).not.toContain("slot");
    });

    test("ordinary single sources retain existing rules while group expansion needs a real asset", () => {
        const empty = node("empty", CanvasNodeType.Image, { parentId: "group" });
        const nodes = [group(), empty, target];
        expect(resolveCanvasReferenceSources(["empty", "empty"], nodes).sourceNodeIds).toEqual(["empty"]);
        expect(planCanvasReferenceConnections({ sourceNodeIds: ["empty"], targetNodeId: "target", nodes, connections: [], config }).connected).toEqual(["empty"]);
        expect(plan(nodes).connected).toEqual([]);
        expect(plan(nodes).skipped[0].nodeId).toBe("empty");
    });

    test("empty, missing and malformed cyclic groups terminate without virtual frame edges", () => {
        const nodes = [group("group", { parentId: "nested" }), group("nested", { parentId: "group" }), target];
        expect(plan(nodes).connections).toEqual([]);
        expect(plan(nodes).skipped).toEqual([{ nodeId: "group", reason: "分组内没有可引用的素材" }]);
        expect(resolveCanvasGroupReferenceSources("missing", nodes).skipped).toEqual([{ nodeId: "missing", reason: "找不到可引用的分组" }]);
        expect(resolveCanvasGroupReferenceSources("target", nodes).sourceNodeIds).toEqual([]);
    });
});

describe("group reference connection plans", () => {
    test("does not report success for references hidden by an existing generation-config receiver", () => {
        const configNode = node("config", CanvasNodeType.Config, { metadata: { generationMode: "video" } });
        const nodes = [group(), image("fresh"), image("existing-a", ""), image("existing-b", ""), configNode, target];
        const connections: CanvasConnection[] = [
            { id: "main", fromNodeId: "target", toNodeId: "config" },
            { id: "b", fromNodeId: "existing-b", toNodeId: "config" },
            { id: "a", fromNodeId: "existing-a", toNodeId: "config" },
        ];
        const before = structuredClone(connections);
        const visible = buildNodeMentionReferences(target, nodes, connections);
        expect(getCanvasReferenceReceiverId("target", nodes, connections)).toBe("config");
        for (const result of [plan(nodes, connections),
            planCanvasReferenceConnections({ sourceNodeIds: ["group"], targetNodeId: "target", nodes, connections, config }),
            planCanvasReferenceConnections({ sourceNodeIds: ["fresh"], targetNodeId: "target", nodes, connections, config })]) {
            expect(result.connections).toEqual([]);
            expect(result.connected).toEqual([]);
            expect(result.duplicates).toEqual([]);
            expect(result.skipped).toEqual([{ nodeId: "fresh", reason: "此节点通过生成配置接收素材，请连接到对应的生成配置节点" }]);
            expect(buildNodeMentionReferences(target, nodes, [...connections, ...result.connections])).toEqual(visible);
        }
        expect(connections).toEqual(before);
        expect(getGenerationResourceNodes("target", nodes, connections).map(item => item.id)).toEqual(["existing-b", "existing-a"]);

        const directConfig = planCanvasReferenceConnections({ sourceNodeIds: ["group"], targetNodeId: "config", nodes, connections, config });
        expect(directConfig.connections.map(edge => [edge.fromNodeId, edge.toNodeId])).toEqual([["fresh", "config"]]);
        expect(getGenerationResourceNodes("target", nodes, [...connections, ...directConfig.connections]).map(item => item.id)).toEqual(["existing-b", "existing-a", "fresh"]);
        const reordered = reorderCanvasResourceConnections("target", ["existing-a", "existing-b"], nodes, connections);
        expect(reordered.map(edge => edge.id)).toEqual(["main", "a", "b"]);
        expect(getGenerationResourceNodes("target", nodes, reordered).map(item => item.id)).toEqual(["existing-a", "existing-b"]);
    });

    test("an empty attached config does not hide direct target references", () => {
        const nodes = [group(), image("fresh"), node("config", CanvasNodeType.Config), target];
        const connections: CanvasConnection[] = [{ id: "main", fromNodeId: "target", toNodeId: "config" }];
        expect(getCanvasReferenceReceiverId("target", nodes, connections)).toBe("target");
        const result = plan(nodes, connections);
        expect(result.connections.map(edge => [edge.fromNodeId, edge.toNodeId])).toEqual([["fresh", "target"]]);
        expect(getGenerationResourceNodes("target", nodes, [...connections, ...result.connections]).map(item => item.id)).toEqual(["fresh"]);
    });

    test("repeated group drops add no duplicates and preserve distinct target handles", () => {
        const nodes = [group(), image("one"), image("two"), target];
        const first = plan(nodes, [], { targetHandleId: "row:1", targetAnchorRatio: 0.3 });
        expect(first.connected).toEqual(["one", "two"]);
        expect(first.connections.every(edge => edge.fromNodeId !== "group" && edge.toHandleId === "row:1" && edge.toAnchorRatio === 0.3)).toBe(true);
        const second = plan(nodes, first.connections, { targetHandleId: "row:1" });
        expect(second.connections).toEqual([]);
        expect(second.duplicates).toEqual(["one", "two"]);
        expect(plan(nodes, first.connections, { targetHandleId: "row:2" }).connections).toHaveLength(2);
    });

    test("real member edges feed storage-only media and text into generation references in group order", () => {
        const nodes = [group(), image("picture"), node("sound", CanvasNodeType.Audio, { parentId: "group", metadata: { storageKey: "media:sound" } }),
            node("text", CanvasNodeType.Text, { parentId: "group", metadata: { content: "Reference text" } }), target];
        const result = plan(nodes);
        expect(result.connected).toEqual(["picture", "sound", "text"]);
        expect(getGenerationResourceNodes("target", nodes, result.connections).map(node => node.id)).toEqual(["picture", "sound", "text"]);
        expect(buildNodeMentionReferences(target, nodes, result.connections).map(reference => [reference.nodeId, reference.kind])).toEqual([["picture", "image"], ["sound", "audio"], ["text", "text"]]);
    });

    test("reports incompatible members and enforces real configured reference capacity", () => {
        const imageTarget = node("target", CanvasNodeType.Image);
        const nodes = [group(), image("one"), image("two"), node("sound", CanvasNodeType.Audio, { parentId: "group", metadata: { content: "sound.wav" } }), imageTarget];
        const capabilityConfig = defaultModelCapabilityConfig(undefined, "one-reference");
        capabilityConfig.image!.references.maxImages = 1;
        const constrainedConfig = { ...config, model: "fixture::one-reference", imageModel: "fixture::one-reference", models: ["fixture::one-reference"], imageModels: ["fixture::one-reference"],
            channels: [{ id: "fixture", name: "fixture", baseUrl: "https://example.test", apiKey: "fixture", apiFormat: "openai" as const, models: ["one-reference"], modelProfiles: [{ model: "one-reference", capability: "image" as const, capabilitySource: "user" as const, capabilityConfig }] }] };
        const result = plan(nodes, [], { config: constrainedConfig, allowCapacityOverflow: true });
        expect(result.connected).toEqual(["one"]);
        expect(result.skipped.find(item => item.nodeId === "two")?.reason).toContain("最多支持");
        expect(result.skipped.find(item => item.nodeId === "sound")?.reason).toContain("参考音频");
        expect(planCanvasReferenceConnections({ sourceNodeIds: ["group"], targetNodeId: "target", nodes, connections: [], config: constrainedConfig, allowCapacityOverflow: true }).connected).toEqual(["one"]);
    });

    test("skips self references and existing indirect cycles while allowing independent members", () => {
        const nodes = [group(), image("ancestor"), image("good"), { ...target, parentId: "group", metadata: { content: "target.mp4" } }, node("middle", CanvasNodeType.Video)];
        const connections: CanvasConnection[] = [{ id: "a", fromNodeId: "target", toNodeId: "middle" }, { id: "b", fromNodeId: "middle", toNodeId: "ancestor" }];
        const result = plan(nodes, connections);
        expect(result.connected).toEqual(["good"]);
        expect(result.skipped.find(item => item.nodeId === "ancestor")?.reason).toContain("循环");
        expect(result.skipped.find(item => item.nodeId === "target")?.reason).toContain("自身");
        expect(connections).toHaveLength(2);
        expect(canvasConnectionError(config, nodes, connections, { fromNodeId: "target", toNodeId: "target" })).toContain("自身");
    });

    test("cycle traversal safely ignores unrelated existing cycles", () => {
        const nodes = [group(), image("one"), target, node("loop-a", CanvasNodeType.Video), node("loop-b", CanvasNodeType.Video)];
        const connections = [{ id: "a", fromNodeId: "target", toNodeId: "loop-a" }, { id: "b", fromNodeId: "loop-a", toNodeId: "loop-b" }, { id: "c", fromNodeId: "loop-b", toNodeId: "loop-a" }];
        expect(plan(nodes, connections).connected).toEqual(["one"]);
    });
});
