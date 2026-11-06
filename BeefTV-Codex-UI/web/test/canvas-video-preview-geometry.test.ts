import { describe, expect, test } from "bun:test";
import { syncCanvasVideoPreviewGeometry } from "../src/lib/canvas/canvas-video-preview-geometry";
import { createCanvasNode } from "../src/lib/canvas/canvas-project-domain";
import { bindDreamaProject } from "../src/lib/canvas/dreama-workbench";
import { defaultModelCapabilityConfig } from "../src/lib/model-capabilities";
import { createModelChannel, defaultConfig, encodeChannelModel } from "../src/stores/use-config-store";
import { CanvasNodeType, type CanvasNodeData, type CanvasNodeMetadata } from "../src/types/canvas";

function settings() {
    const profile = { model: "video", protocol: "dreama-videos-async", capability: "video" as const, capabilityConfig: { version: 1 as const, video: { ...defaultModelCapabilityConfig("dreama-videos-async").video!, ratios: [], defaultRatio: "" } } };
    const channel = createModelChannel({ id: "dreama", interfaceType: "dreama-videos-async", models: ["video"], modelProfiles: [profile] });
    return { ...defaultConfig, channels: [channel], videoModel: encodeChannelModel(channel.id, "video"), size: "16:9" };
}

const binding = (aspectRatio = "9:16") => bindDreamaProject(undefined, { id: 41, name: "项目", promptMode: "drama", aspectRatio }, 10);
const video = (metadata: CanvasNodeMetadata = {}): CanvasNodeData => ({ id: "video", type: CanvasNodeType.Video, title: "视频", width: 720, height: 405, position: { x: 100, y: 200 }, metadata: { model: "dreama::video", nodeRole: "generator", ...metadata } });

describe("empty video preview geometry", () => {
    test("repairs a restored landscape placeholder from the effective project ratio without freezing inheritance", () => {
        const original = video();
        const [result] = syncCanvasVideoPreviewGeometry([original], [], settings(), binding());
        expect(result!.width / result!.height).toBeCloseTo(9 / 16);
        expect(result!.position.x + result!.width / 2).toBeCloseTo(460);
        expect(result!.position.y + result!.height / 2).toBeCloseTo(402.5);
        expect(result!.metadata).toBe(original.metadata);
        expect(result!.metadata?.size).toBeUndefined();
        expect(original.width).toBe(720);
        expect(original.height).toBe(405);
    });

    test("new empty nodes follow later project ratio changes and settle without repeated document changes", () => {
        const node = createCanvasNode(CanvasNodeType.Video, { x: 400, y: 400 }, { model: "dreama::video" });
        let nodes = [node];
        for (const [ratio, expected] of [["9:16", 9 / 16], ["16:9", 16 / 9], ["1:1", 1]] as const) {
            nodes = syncCanvasVideoPreviewGeometry(nodes, [], settings(), binding(ratio));
            expect(nodes[0]!.width / nodes[0]!.height).toBeCloseTo(expected);
            expect(nodes[0]!.position.x + nodes[0]!.width / 2).toBeCloseTo(400);
            expect(nodes[0]!.position.y + nodes[0]!.height / 2).toBeCloseTo(400);
            expect(nodes[0]!.metadata?.size).toBeUndefined();
            expect(syncCanvasVideoPreviewGeometry(nodes, [], settings(), binding(ratio))).toBe(nodes);
        }
    });

    test("a saved node ratio remains authoritative when it conflicts with the project", () => {
        const original = video({ size: "1:1" });
        const [result] = syncCanvasVideoPreviewGeometry([original], [], settings(), binding());
        expect(result!.width / result!.height).toBeCloseTo(1);
        expect(result!.metadata).toBe(original.metadata);
    });

    test("loading placeholders follow the ratio while completed and uploaded media keep their geometry", () => {
        const protectedNodes = [
            video({ content: "https://example.test/video.mp4" }),
            video({ storageKey: "video.mp4" }),
            video({ previewContent: "preview" }),
            video({ fileUpload: "uploading" }),
        ];
        expect(syncCanvasVideoPreviewGeometry(protectedNodes, [], settings(), binding())).toBe(protectedNodes);
        const [loading] = syncCanvasVideoPreviewGeometry([video({ status: "loading", taskId: "task" })], [], settings(), binding());
        expect(loading!.width / loading!.height).toBeCloseTo(9 / 16);
        expect(loading!.metadata?.taskId).toBe("task");
    });

    test("manual, free resize, locked, workflow and non-video nodes are untouched", () => {
        const protectedNodes = [
            video({ manualSize: true }), video({ freeResize: true }), video({ locked: true }),
            video({ workflowSlot: { instanceId: "instance", nodeId: "slot", role: "input", mediaType: "video" } }),
            { ...video(), type: CanvasNodeType.Image },
        ];
        expect(syncCanvasVideoPreviewGeometry(protectedNodes, [], settings(), binding())).toBe(protectedNodes);
    });
});
