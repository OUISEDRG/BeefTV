import { describe, expect, test } from "bun:test";
import { compileDreamaCanvas } from "../src/lib/canvas/dreama-canvas-compiler";
import { createModelChannel, defaultConfig, encodeChannelModel } from "../src/stores/use-config-store";
import { defaultModelCapabilityConfig } from "../src/lib/model-capabilities";
import { CanvasNodeType } from "../src/types/canvas";

function fixture() {
    const channel = createModelChannel({ id: "dreama", enabled: true, baseUrl: "http://127.0.0.1:18765", apiKey: "secret-not-in-output", interfaceType: "dreama-videos-async", models: ["video"], modelProfiles: [{ model: "video", capability: "video", protocol: "dreama-videos-async", videoCapabilitiesVersion: "v1", capabilityConfig: { version: 1, video: { ...defaultModelCapabilityConfig("dreama-videos-async").video!, operations: ["reference_to_video"], references: { maxImages: 9, maxVideos: 1, maxAudios: 0 } } } }] });
    const model = encodeChannelModel(channel.id, "video");
    const base = { position: { x: 0, y: 0 }, width: 100, height: 100 };
    return {
        schemaVersion: 1 as const,
        config: { ...defaultConfig, channels: [channel], model, videoModel: model, videoSeconds: "5", vquality: "720" },
        nodeIds: ["target"], assets: [],
        canvas: { id: "canvas", revision: 3, integrations: { dreama: { schemaVersion: 1 as const, projectId: 41, projectName: "剧", promptMode: "drama" as const, aspectRatio: "16:9", defaultSeriesId: 11, revision: 2 } }, nodes: [
            { ...base, id: "scene", type: CanvasNodeType.Image, title: "场景", metadata: { storageKey: "resource:scene", content: "/api/resources/scene/file" } },
            { ...base, id: "person", type: CanvasNodeType.Image, title: "人物", metadata: { storageKey: "resource:person", content: "/api/resources/person/file", dreamaVoiceBinding: { version: 1, name: "人物", source: "catalog", voiceId: "voice-2" } } },
            { ...base, id: "episode", type: CanvasNodeType.Frame, title: "第2集", metadata: { dreamaSeriesId: 12 } },
            { ...base, id: "target", type: CanvasNodeType.Video, title: "镜1", parentId: "episode", metadata: { model, seconds: "5", vquality: "720", size: "16:9", videoEditOperation: "reference_to_video", composerContent: "  @[node:person] 说话。\n@[node:scene]，@[node:person] 再说。  " } },
        ], connections: [{ id: "a", fromNodeId: "scene", toNodeId: "target" }, { id: "b", fromNodeId: "person", toNodeId: "target" }] },
    };
}
describe("headless canvas compiler", () => {
    test("keeps uploaded voice samples as resource identities without embedding audio", async () => {
        const input = fixture();
        input.canvas.nodes[1]!.metadata.dreamaVoiceBinding = { version: 1, name: "人物", source: "resource", sampleResourceId: "owned-sample" } as any;
        const before = JSON.stringify(input);
        const item = (await compileDreamaCanvas(input as any)).items[0]!;
        expect(item.metadata.providerOptions).toMatchObject({ "dreama-videos-async": {
            character_voices: [{ image_index: 1, name: "人物", sampleResourceId: "owned-sample" }],
        } });
        expect(item.preview.voices).toEqual([{ image_index: 1, name: "人物", sample: true }]);
        expect(JSON.stringify(item)).not.toContain("data:audio/");
        expect(JSON.stringify(input)).toBe(before);
    });
    test("uses UI reference order, preserves prose and freezes group/voice without credentials or mutation", async () => {
        const input = fixture(); const before = JSON.stringify(input);
        const result = await compileDreamaCanvas(input as any);
        expect(result.items[0]?.preview).toMatchObject({ binding: { projectId: 41, seriesId: 12 }, prompt: "  @图片1 说话。\n@图片2，@图片1 再说。  ", voices: [{ image_index: 1, name: "人物", voice_id: "voice-2" }] });
        expect(JSON.stringify(result)).not.toContain("secret-not-in-output");
        expect(JSON.stringify(input)).toBe(before);
        input.canvas.integrations.dreama.defaultSeriesId = 99;
        expect(result.items[0]?.preview.binding.seriesId).toBe(12);
    });
    test("legacy numbered references keep their original connection order", async () => {
        const input = fixture(); input.canvas.nodes[3]!.metadata.composerContent = " @图片2，@图片1，再 @图片2。 ";
        const item = (await compileDreamaCanvas(input as any)).items[0]!;
        expect(item.preview.prompt).toBe(" @图片2，@图片1，再 @图片2。 ");
        expect(item.preview.voices[0]?.image_index).toBe(2);
    });
    test("rejects stale explicit parameters before normalizing defaults", async () => {
        const input = fixture(); input.canvas.nodes[3]!.metadata.seconds = "999";
        await expect(compileDreamaCanvas(input as any)).rejects.toThrow("时长");
    });
    test("rejects unused or unsaved assets and missing bindings", async () => {
        const input = fixture(); input.canvas.nodes[3]!.metadata.composerContent = "@[node:person]";
        await expect(compileDreamaCanvas(input as any)).rejects.toThrow("尚未在提示词中引用");
        const unsaved = fixture(); unsaved.canvas.nodes[0]!.metadata.storageKey = "";
        await expect(compileDreamaCanvas(unsaved as any)).rejects.toThrow();
        const unbound = fixture(); delete (unbound.canvas as any).integrations;
        await expect(compileDreamaCanvas(unbound as any)).rejects.toThrow("绑定");
    });
    test("rejects project ratio conflicts and unknown models", async () => {
        const input = fixture(); input.canvas.nodes[3]!.metadata.size = "9:16";
        await expect(compileDreamaCanvas(input as any)).rejects.toThrow("不一致");
        input.canvas.nodes[3]!.metadata.model = "dreama::removed";
        await expect(compileDreamaCanvas(input as any)).rejects.toThrow("型号");
    });
});
