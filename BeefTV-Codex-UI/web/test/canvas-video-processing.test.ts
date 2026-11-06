import { describe, expect, test } from "bun:test";
import { canvasVideoOperationSupport } from "../src/lib/canvas/canvas-provider-capabilities";
import { createCanvasVideoProcessingDraft } from "../src/lib/canvas/canvas-video-processing";
import { compileDreamaCanvas } from "../src/lib/canvas/dreama-canvas-compiler";
import { defaultModelCapabilityConfig } from "../src/lib/model-capabilities";
import { videoSettingsConstraints } from "../src/lib/video-settings-constraints";
import { createModelChannel, defaultConfig, type AiConfig } from "../src/stores/use-config-store";
import { CanvasNodeType, type CanvasNodeData } from "../src/types/canvas";

function fixture() {
    const channel = createModelChannel({ id: "processing", enabled: true, baseUrl: "http://127.0.0.1:18765", apiKey: "synthetic", models: ["enhance-one", "enhance-two", "extend"], modelProfiles: [
        ...["enhance-one", "enhance-two"].map(model => ({ model, capability: "video" as const, protocol: "dreama-enhancement-async", videoCapabilitiesVersion: "v1", capabilityConfig: { version: 1 as const, video: {
            ...defaultModelCapabilityConfig("dreama-enhancement-async").video!, operations: ["upscale"], defaultOperation: "upscale", promptRequired: false,
            references: { minImages: 0, maxImages: 0, maxVideos: 1, maxAudios: 0 },
            durationSupported: false, resolutions: [], ratios: [], defaultResolution: "", defaultRatio: "",
            generateAudio: { supported: false, default: false }, watermark: { supported: false, default: false },
        } } })),
        { model: "extend", capability: "video", protocol: "dreama-extension-async", videoCapabilitiesVersion: "v1", capabilityConfig: { version: 1, video: {
            ...defaultModelCapabilityConfig("dreama-extension-async").video!, operations: ["extend"], defaultOperation: "extend", references: { minImages: 0, maxImages: 0, maxVideos: 1, maxAudios: 0 },
        } } },
    ] });
    const config: AiConfig = { ...defaultConfig, channels: [channel], model: "processing::extend", videoModel: "processing::extend" };
    const source: CanvasNodeData = { id: "source", type: CanvasNodeType.Video, title: "原片", parentId: "episode", position: { x: 20, y: 30 }, width: 320, height: 180,
        metadata: { content: "/api/resources/source/file", storageKey: "resource:source", model: "processing::extend", seconds: "12", vquality: "720p", generateAudio: "true", status: "success", taskId: "original-task" } };
    return { config, channel, source };
}

describe("canvas video processing shortcuts", () => {
    test("enhancement requires an enabled, credentialed adapter declaring upscale", () => {
        const { config, channel, source } = fixture();
        expect(canvasVideoOperationSupport(config, source, "upscale")).toMatchObject({ available: true, model: "processing::enhance-one" });
        source.metadata!.model = "processing::enhance-two";
        expect(canvasVideoOperationSupport(config, source, "upscale").model).toBe("processing::enhance-two");
        const unavailableChannels = [
            { ...channel, enabled: false },
            { ...channel, apiKey: "" },
            { ...channel, models: ["extend"] },
            { ...channel, modelProfiles: channel.modelProfiles?.map(profile => ({ ...profile, capabilityConfig: { version: 1 as const, video: { ...profile.capabilityConfig!.video!, operations: ["reference_to_video"] } } })) },
        ];
        expect(unavailableChannels.map(unavailable => canvasVideoOperationSupport({ ...config, channels: [unavailable] }, source, "upscale").available)).toEqual([false, false, false, false]);
    });

    test.each(["extend", "upscale"] as const)("%s creates one connected idle draft and retains the source and its episode", operation => {
        const { config, source } = fixture();
        const before = JSON.stringify(source);
        const { node, connection } = createCanvasVideoProcessingDraft(config, source, operation);
        expect(node.type).toBe(CanvasNodeType.Video);
        expect(node.parentId).toBe("episode");
        expect(node.id).not.toBe(source.id);
        expect(node.metadata).toMatchObject({ status: "idle", videoEditOperation: operation, generatedFromNodeId: source.id });
        expect(node.metadata?.taskId).toBeUndefined();
        expect(node.metadata?.storageKey).toBeUndefined();
        expect(connection).toMatchObject({ fromNodeId: source.id, toNodeId: node.id });
        expect(JSON.stringify(source)).toBe(before);
        if (operation === "upscale") {
            expect(node.metadata?.composerContent).toBe("");
            expect(node.metadata?.seconds).toBeUndefined();
            expect(node.metadata?.vquality).toBeUndefined();
            expect(node.metadata?.generateAudio).toBe("false");
        } else expect(node.metadata?.composerContent).toContain("@视频1");
    });

    test("a storage-backed source works without a preview URL; empty and wrong media cannot prepare", () => {
        const { config, source } = fixture();
        delete source.metadata!.content;
        expect(createCanvasVideoProcessingDraft(config, source, "upscale").node.metadata?.videoEditOperation).toBe("upscale");
        expect(() => createCanvasVideoProcessingDraft(config, { ...source, metadata: {} }, "upscale")).toThrow("可读取的视频");
        expect(() => createCanvasVideoProcessingDraft(config, { ...source, type: CanvasNodeType.Image }, "upscale")).toThrow("可读取的视频");
    });

    test("whole-video enhancement displays source duration instead of a global generation duration", () => {
        const { config, source } = fixture();
        const { node } = createCanvasVideoProcessingDraft(config, source, "upscale");
        expect(videoSettingsConstraints({ ...config, model: node.metadata!.model!, videoSeconds: "6" }, { videoCount: 1 }, node.metadata)).toEqual({ durationLocked: true });
        expect(videoSettingsConstraints(config, { videoCount: 1 }, { videoEditOperation: "extend" }).durationLocked).not.toBe(true);
    });

    test("enhancement draft compiles without prompt or generation and keeps its single original video binding", async () => {
        const { config, source } = fixture();
        const { node, connection } = createCanvasVideoProcessingDraft(config, source, "upscale");
        const canvas = { id: "canvas", revision: 1, integrations: { dreama: { schemaVersion: 1 as const, projectId: 41, projectName: "剧", promptMode: "drama" as const, aspectRatio: "16:9", defaultSeriesId: 11, revision: 1 } },
            nodes: [source, node, { id: "episode", type: CanvasNodeType.Frame, title: "第2集", position: { x: 0, y: 0 }, width: 100, height: 100, metadata: { dreamaSeriesId: 12 } }], connections: [connection] };
        const item = (await compileDreamaCanvas({ schemaVersion: 1, config, canvas, nodeIds: [node.id], assets: [] })).items[0]!;
        expect(item.metadata.videoEditOperation).toBe("upscale");
        expect(item.prompt).toBe("");
        expect(item.referenceVideos).toEqual([expect.objectContaining({ id: "source", storageKey: "resource:source" })]);
        expect(item.preview.binding.seriesId).toBe(12);
        expect(item.config.interfaceType).toBe("dreama-enhancement-async");
    });
});
