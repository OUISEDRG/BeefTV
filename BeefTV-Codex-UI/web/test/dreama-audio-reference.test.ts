import { describe, expect, test } from "bun:test";
import { buildNodeGenerationContext, compileNodeGenerationReferences, hydrateNodeGenerationContext } from "../src/components/canvas/canvas-node-generation";
import { compileDreamaCanvas } from "../src/lib/canvas/dreama-canvas-compiler";
import { buildVideoGenerationMetadata } from "../src/lib/canvas/canvas-project-generation";
import { prepareDreamaVoiceProviderOptions } from "../src/lib/canvas/dreama-voice-generation";
import { defaultModelCapabilityConfig } from "../src/lib/model-capabilities";
import { prepareBackendGenerationTask } from "../src/services/api/generation-task";
import { createModelChannel, defaultConfig, encodeChannelModel } from "../src/stores/use-config-store";
import { CanvasNodeType, type CanvasNodeData } from "../src/types/canvas";

// Ordinary audio limits from the upstream 九梦 2.5 Pro catalog, projected by
// the bridge. No endpoint is called: all media below are owned-resource identities.
function fixture(audioCount = 2, includeImage = true, operation?: string, videoCount = 0) {
    const video = {
        ...defaultModelCapabilityConfig("dreama-videos-async").video!,
        operations: ["text_to_video", "image_to_video", "reference_to_video", "audio_to_video"],
        defaultOperation: "text_to_video",
        references: { minImages: 0, maxImages: 30, maxVideos: 10, maxAudios: 10, maxVideoBytes: 200 * 1024 * 1024, minVideoDurationSeconds: 2, maxVideoDurationSeconds: 30, maxVideoTotalDurationSeconds: 30, maxAudioBytes: 15 * 1024 * 1024, minAudioDurationSeconds: 2, maxAudioDurationSeconds: 30, maxAudioTotalDurationSeconds: 30 },
        operationReferences: {
            text_to_video: { minImages: 0, maxImages: 0, maxVideos: 0, maxAudios: 0 },
            image_to_video: { minImages: 1, maxImages: 30, maxVideos: 0, maxAudios: 10 },
            reference_to_video: { minImages: 0, maxImages: 30, maxVideos: 10, maxAudios: 10 },
            audio_to_video: { minImages: 0, maxImages: 0, maxVideos: 0, maxAudios: 10 },
        },
    };
    const channel = createModelChannel({ id: "dreama", enabled: true, baseUrl: "http://127.0.0.1:18765", apiKey: "offline-test-key", interfaceType: "dreama-videos-async", models: ["video"], modelProfiles: [{ model: "video", capability: "video", protocol: "dreama-videos-async", videoCapabilitiesVersion: "ordinary-audio-v1", referenceMediaTransport: { image: "inline", audio: "inline", video: "inline" }, capabilityConfig: { version: 1, video } }] });
    const model = encodeChannelModel(channel.id, "video");
    const config = { ...defaultConfig, channels: [channel], model, videoModel: model, videoSeconds: "5", vquality: "720", size: "16:9" };
    const base = { position: { x: 0, y: 0 }, width: 100, height: 100 };
    const audios: CanvasNodeData[] = Array.from({ length: audioCount }, (_, index) => ({ ...base, id: `audio-${index + 1}`, type: CanvasNodeType.Audio, title: `声音${index + 1}`, metadata: { storageKey: `resource:audio-${index + 1}`, durationMs: 2_000, bytes: 5_000, mimeType: "audio/mpeg" } }));
    const images: CanvasNodeData[] = includeImage ? [{ ...base, id: "person", type: CanvasNodeType.Image, title: "人物", metadata: { storageKey: "resource:person", dreamaVoiceBinding: { version: 1, source: "resource", name: "人物音色", sampleResourceId: "voice-sample" } } }] : [];
    const videos: CanvasNodeData[] = Array.from({ length: videoCount }, (_, index) => ({ ...base, id: `video-${index + 1}`, type: CanvasNodeType.Video, title: `参考片段${index + 1}`, metadata: { storageKey: `resource:video-${index + 1}`, durationMs: 2_000, bytes: 8_000, naturalWidth: 1280, naturalHeight: 720, mimeType: "video/mp4" } }));
    const prompt = `  ${audios.toReversed().map(node => `@[node:${node.id}]`).join("，")}\n${videos.length ? videos.toReversed().map(node => `@[node:${node.id}]`).join("，") + "\n" : ""}${includeImage ? "@[node:person] 随节奏动作。" : "按声音节奏生成画面。"}  `;
    const target: CanvasNodeData = { ...base, id: "target", type: CanvasNodeType.Video, title: "镜头", metadata: { model, composerContent: prompt, seconds: "5", vquality: "720", size: "16:9", ...(operation ? { videoEditOperation: operation as any } : {}) } };
    const nodes = [...images, ...audios, ...videos, target];
    return {
        schemaVersion: 1 as const, config, nodeIds: ["target"], assets: [] as never[],
        canvas: { id: "canvas", revision: 3, integrations: { dreama: { schemaVersion: 1 as const, projectId: 41, projectName: "离线测试", promptMode: "drama" as const, aspectRatio: "16:9", defaultSeriesId: 11, revision: 2 } }, nodes, connections: [...images, ...audios, ...videos].map(node => ({ id: `edge-${node.id}`, fromNodeId: node.id, toNodeId: "target" })) },
    };
}

describe("Dreama ordinary audio reference contract", () => {
    test.each([true, false])("compiles saved audio alongside image=%s without losing ordinary audio or conflating voices", async includeImage => {
        const input = fixture(2, includeImage);
        const before = JSON.stringify(input);
        const item = (await compileDreamaCanvas(input)).items[0]!;
        expect(item.referenceAudios.map(audio => audio.storageKey)).toEqual(["resource:audio-1", "resource:audio-2"]);
        expect(item.referenceAudios.map(audio => audio.durationMs)).toEqual([2_000, 2_000]);
        expect(item.prompt).toBe(`  @音频2，@音频1\n${includeImage ? "@图片1 随节奏动作。" : "按声音节奏生成画面。"}  `);
        expect(item.metadata.videoEditOperation).toBe(includeImage ? "image_to_video" : "audio_to_video");
        expect(item.preview.media.filter(media => media.kind === "音频").map(media => media.id)).toEqual(["audio-1", "audio-2"]);
        expect(item.preview.voices).toEqual(includeImage ? [{ image_index: 1, name: "人物音色", sample: true }] : []);
        expect(JSON.stringify(input)).toBe(before);
        expect(JSON.stringify(item)).not.toContain("offline-test-key");
    });

    test("explicit multimodal compilation keeps audio first-mention numbering and voice image indices", async () => {
        const item = (await compileDreamaCanvas(fixture(2, true, "reference_to_video"))).items[0]!;
        expect(item.referenceAudios.map(audio => audio.id)).toEqual(["audio-2", "audio-1"]);
        expect(item.prompt).toBe("  @音频1，@音频2\n@图片1 随节奏动作。  ");
        expect(item.preview.voices).toEqual([{ image_index: 1, name: "人物音色", sample: true }]);
    });

    test("keeps every audio at the catalog count limit and rejects overflow without pruning or modifying the canvas", async () => {
        const valid = await compileDreamaCanvas(fixture(10, false));
        expect(valid.items[0]!.referenceAudios).toHaveLength(10);
        const invalid = fixture(11, false);
        const before = JSON.stringify(invalid);
        await expect(compileDreamaCanvas(invalid)).rejects.toThrow();
        expect(JSON.stringify(invalid)).toBe(before);
    });

    test("UI hydration and task serialization preserve local audio identities separately from voice samples", async () => {
        const input = fixture();
        const target = input.canvas.nodes.at(-1)!;
        let context = buildNodeGenerationContext("target", input.canvas.nodes, input.canvas.connections, target.metadata!.composerContent!, [], true, { preserveMediaBindings: true, preservePromptText: true, referenceOnly: true });
        context = await hydrateNodeGenerationContext(context, "canvas", undefined, "video", true, false, { maxImages: 30, maxVideos: 0, maxAudios: 10 }, true);
        const metadata = buildVideoGenerationMetadata(target, context, input.config);
        context = compileNodeGenerationReferences(context, input.config, metadata.videoEditOperation, { referenceOnly: true });
        const providerOptions = await prepareDreamaVoiceProviderOptions(context, input.config, "video", async id => id);
        const task = await prepareBackendGenerationTask({ mode: "video", prompt: context.prompt, config: input.config, referenceImages: context.referenceImages, referenceAudios: context.referenceAudios, metadata: { ...metadata, providerOptions } });
        const body = task.input as any;
        expect(body.referenceAudios.map((audio: any) => ({ id: audio.id, storageKey: audio.storageKey, durationMs: audio.durationMs, bytes: audio.bytes }))).toEqual([
            { id: "audio-1", storageKey: "resource:audio-1", durationMs: 2_000, bytes: 5_000 },
            { id: "audio-2", storageKey: "resource:audio-2", durationMs: 2_000, bytes: 5_000 },
        ]);
        expect(body.metadata.providerOptions["dreama-videos-async"].character_voices).toEqual([{ image_index: 1, name: "人物音色", sampleResourceId: "voice-sample" }]);
        expect(body.metadata.videoEditOperation).toBe("image_to_video");
    });

    test("multiple ordinary videos and audio compile as multimodal references with every slot and metadata intact", async () => {
        const input = fixture(2, true, undefined, 3);
        const before = JSON.stringify(input);
        const item = (await compileDreamaCanvas(input)).items[0]!;
        expect(item.metadata.videoEditOperation).toBe("reference_to_video");
        expect(item.referenceVideos.map(video => video.storageKey)).toEqual(["resource:video-3", "resource:video-2", "resource:video-1"]);
        expect(item.referenceAudios.map(audio => audio.storageKey)).toEqual(["resource:audio-2", "resource:audio-1"]);
        expect(item.prompt).toBe("  @音频1，@音频2\n@视频1，@视频2，@视频3\n@图片1 随节奏动作。  ");
        expect(item.preview.media.filter(media => media.kind === "视频").map(media => media.id)).toEqual(["video-3", "video-2", "video-1"]);
        const target = input.canvas.nodes.at(-1)!;
        let context = buildNodeGenerationContext("target", input.canvas.nodes, input.canvas.connections, target.metadata!.composerContent!, [], true, { preserveMediaBindings: true, preservePromptText: true, referenceOnly: true });
        context = await hydrateNodeGenerationContext(context, "canvas", undefined, "video", true, false, { maxImages: 30, maxVideos: 10, maxAudios: 10 }, true);
        context = compileNodeGenerationReferences(context, input.config, item.metadata.videoEditOperation, { referenceOnly: true });
        const task = await prepareBackendGenerationTask({ mode: "video", prompt: context.prompt, config: input.config, referenceImages: context.referenceImages, referenceVideos: context.referenceVideos, referenceAudios: context.referenceAudios, metadata: item.metadata });
        const body = task.input as any;
        expect(body.referenceVideos.map((video: any) => ({ id: video.id, storageKey: video.storageKey, durationMs: video.durationMs, bytes: video.bytes, width: video.width, height: video.height }))).toEqual([3, 2, 1].map(index => ({ id: `video-${index}`, storageKey: `resource:video-${index}`, durationMs: 2_000, bytes: 8_000, width: 1280, height: 720 })));
        expect(body.referenceAudios.map((audio: any) => audio.id)).toEqual(["audio-2", "audio-1"]);
        expect(body.metadata.videoEditOperation).toBe("reference_to_video");
        expect(JSON.stringify(input)).toBe(before);
    });

    test("multiple-video limit is enforced without treating ordinary references as one extension source", async () => {
        expect((await compileDreamaCanvas(fixture(0, false, undefined, 10))).items[0]!.referenceVideos).toHaveLength(10);
        await expect(compileDreamaCanvas(fixture(0, false, undefined, 11))).rejects.toThrow();
    });
});
