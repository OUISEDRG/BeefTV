import { describe, expect, test } from "bun:test";
import { buildNodeGenerationContext, compileNodeGenerationReferences, type NodeGenerationContext } from "../src/components/canvas/canvas-node-generation";
import { buildDreamaCharacterVoices, prepareDreamaAudioIdentity, prepareDreamaVoiceProviderOptions } from "../src/lib/canvas/dreama-voice-generation";
import { canvasGenerationRequestFingerprint } from "../src/lib/canvas/canvas-generation-submission";
import { prepareBackendGenerationTask } from "../src/services/api/generation-task";
import { createModelChannel, defaultConfig, encodeChannelModel } from "../src/stores/use-config-store";
import { CanvasNodeType, type CanvasNodeData } from "../src/types/canvas";
import type { DreamaVoiceBinding } from "../src/types/dreama-voice";
import type { ReferenceImage } from "../src/types/image";
import { defaultModelCapabilityConfig } from "../src/lib/model-capabilities";

const catalog: DreamaVoiceBinding = { version: 1, name: "主角", source: "catalog", voiceId: "voice-42" };
const sample: DreamaVoiceBinding = { version: 1, name: "女主", source: "resource", sampleResourceId: "res-audio" };
const audioData = "data:audio/mpeg;base64,YXVkaW8=";
function image(id: string, voice?: DreamaVoiceBinding): ReferenceImage {
    return { id, name: id, type: "image/png", dataUrl: "", storageKey: `resource:${id}`, dreamaVoiceBinding: voice };
}
function context(images: ReferenceImage[]): NodeGenerationContext {
    return { prompt: "  台词原文：你好！\n不要增加旁白。  ", referenceImages: images, referenceVideos: [], referenceAudios: [],
        characterReferences: [], resolvedCharacterVersions: [], resolvedCharacterVoices: [], textCount: 1,
        imageCount: images.length, videoCount: 0, audioCount: 0 };
}
function config(protocol: string, mode: "video" | "audio" = "video") {
    const channel = createModelChannel({ id: "local-dreama", name: "Local", baseUrl: "http://127.0.0.1:18765", apiKey: "test-key", interfaceType: protocol,
        models: ["model-kept"], modelProfiles: [{ model: "model-kept", capability: mode, protocol,
            defaultOptions: { retainedOption: "kept" },
            ...(mode === "video" ? { capabilityConfig: { version: 1, video: { ...defaultModelCapabilityConfig(protocol).video!, references: { maxImages: 9, maxVideos: 1, maxAudios: 0 }, operations: ["reference_to_video"] } } } : {}),
        }] });
    return { ...defaultConfig, channels: [channel], model: encodeChannelModel(channel.id, "model-kept"), vquality: "720", videoSeconds: "7" };
}
const noSample = async () => { throw new Error("Catalog voice must not read a sample"); };

describe("Dreama ordinary-image voice generation", () => {
    test("freezes binding from the image while preserving its id, data and connection", () => {
        const source: CanvasNodeData = { id: "person", type: CanvasNodeType.Image, title: "人物", position: { x: 0, y: 0 }, width: 20, height: 20,
            metadata: { content: "data:image/png;base64,YQ==", storageKey: "resource:person", dreamaVoiceBinding: { ...catalog } } };
        const target: CanvasNodeData = { ...source, id: "target", type: CanvasNodeType.Video, metadata: {} };
        const nodes = [source, target];
        const edges = [{ id: "edge", fromNodeId: "person", toNodeId: "target" }];
        const before = structuredClone({ nodes, edges });
        const result = buildNodeGenerationContext("target", nodes, edges, "原文", [], true, { preserveMediaBindings: true });
        expect(result.referenceImages[0]?.dreamaVoiceBinding).toEqual(catalog);
        expect(result.referenceImages[0]?.id).toBe("person");
        expect(result.referenceImages[0]?.dataUrl).toBe(source.metadata.content!);
        expect({ nodes, edges }).toEqual(before);
        source.metadata.dreamaVoiceBinding!.voiceId = "changed-later";
        expect(result.referenceImages[0]?.dreamaVoiceBinding?.voiceId).toBe("voice-42");
    });

    test("uses final reference order after token compilation, including gaps without voices", async () => {
        const initial = { ...context([image("one", catalog), image("unbound"), image("two", sample)]),
            prompt: "@[node:two] 和 @[node:one] 说话，背景保持。",
            boundMediaMentions: [{ token: "@[node:two]", kind: "image" as const, referenceId: "two" },
                { token: "@[node:one]", kind: "image" as const, referenceId: "one" }] };
        const final = compileNodeGenerationReferences(initial, config("dreama-extension-async"), "reference_to_video");
        expect(final.referenceImages.map(item => item.id)).toEqual(["two", "one", "unbound"]);
        const calls: string[] = [];
        const voices = await buildDreamaCharacterVoices(final.referenceImages, undefined, async id => { calls.push(id); return id; });
        expect(voices).toEqual([{ image_index: 1, name: "女主", sampleResourceId: "res-audio" }, { image_index: 2, name: "主角", voice_id: "voice-42" }]);
        expect(calls).toEqual(["res-audio"]);
        expect(final.prompt).toBe("@图片1 和 @图片2 说话，背景保持。");
    });

    test("audio consumes one image as identity only without changing dialogue", async () => {
        const before = context([image("person", catalog)]);
        const snapshot = structuredClone(before);
        const prepared = prepareDreamaAudioIdentity(before, "audio");
        expect(prepared.referenceImages).toEqual([]);
        expect(prepared.imageCount).toBe(0);
        expect(prepared.prompt).toBe(snapshot.prompt);
        expect(before).toEqual(snapshot);
        const options = await prepareDreamaVoiceProviderOptions(prepared, config("dreama-audio-async", "audio"), "audio", noSample);
        expect(options).toEqual({ "dreama-audio-async": { character_voices: [{ image_index: 1, name: "主角", voice_id: "voice-42" }] } });
    });

    test("multiple speakers, other image/audio references and legacy character cards fail before submission", () => {
        expect(() => prepareDreamaAudioIdentity(context([image("a", catalog), image("b", sample)]), "audio")).toThrow("只能连接一张");
        expect(() => prepareDreamaAudioIdentity(context([image("a", catalog), image("b")]), "audio")).toThrow("其他参考图片");
        expect(() => prepareDreamaAudioIdentity({ ...context([image("a", catalog)]), referenceAudios: [{ id: "a", name: "a", type: "audio/mpeg", url: "data:audio/mpeg;base64,YQ==" }] }, "audio")).toThrow("音频");
        expect(() => prepareDreamaAudioIdentity({ ...context([image("a", catalog)]), characterReferences: [{ nodeId: "legacy", assetId: "character" }] }, "audio")).toThrow("只能连接一张");
    });

    test("removed bindings remain ordinary images and do not read samples or add provider options", async () => {
        const input = context([image("a")]);
        expect(prepareDreamaAudioIdentity(input, "audio")).toBe(input);
        expect(await prepareDreamaVoiceProviderOptions(input, config("another-provider"), "video", noSample)).toBeUndefined();
    });

    test("unknown protocols, malformed bindings, duplicate identity and unsafe samples fail closed", async () => {
        await expect(prepareDreamaVoiceProviderOptions(context([image("a", catalog)]), config("seedance-videos-compatible"), "video", noSample)).rejects.toThrow("不支持");
        await expect(buildDreamaCharacterVoices([image("a", { ...catalog, voiceId: "../file" })], undefined, noSample)).rejects.toThrow("已失效");
        await expect(buildDreamaCharacterVoices([image("a", catalog), image("a", sample)], undefined, async id => id)).rejects.toThrow("重复引用");
        await expect(buildDreamaCharacterVoices([image("a", sample)], undefined, async () => "D:\\private\\sound.mp3")).rejects.toThrow("已保存的音频资源");
        await expect(buildDreamaCharacterVoices([image("a", sample)], undefined, async () => audioData)).rejects.toThrow("已保存的音频资源");
        await expect(buildDreamaCharacterVoices([image("a", sample)], undefined, async () => "different-resource")).rejects.toThrow("已保存的音频资源");
    });

    test("uploaded voice samples remain resource references in a persisted audio task", async () => {
        const prepared = prepareDreamaAudioIdentity(context([image("person", sample)]), "audio");
        const selected = config("dreama-audio-async", "audio");
        const providerOptions = await prepareDreamaVoiceProviderOptions(prepared, selected, "audio", async id => id);
        const task = await prepareBackendGenerationTask({ mode: "audio", prompt: prepared.prompt, config: selected,
            referenceImages: prepared.referenceImages, metadata: { providerOptions } });
        expect(task.input.metadata).toMatchObject({ providerOptions: {
            "dreama-audio-async": { character_voices: [{ image_index: 1, name: "女主", sampleResourceId: "res-audio" }] },
        } });
        expect(JSON.stringify(task.input)).not.toContain("data:audio/");
    });

    test("backend submission snapshot preserves prompt and namespaced options plus existing defaults", async () => {
        const prepared = prepareDreamaAudioIdentity(context([image("person", catalog)]), "audio");
        const selected = config("dreama-audio-async", "audio");
        const providerOptions = await prepareDreamaVoiceProviderOptions(prepared, selected, "audio", noSample);
        const task = await prepareBackendGenerationTask({ mode: "audio", prompt: prepared.prompt, config: selected,
            referenceImages: prepared.referenceImages, metadata: { providerOptions, retainedMetadata: true } });
        expect(task.prompt).toBe(prepared.prompt);
        expect(task.input.prompt).toBe(prepared.prompt);
        expect(task.input.referenceImages).toEqual([]);
        expect(task.input.metadata).toMatchObject({ retainedMetadata: true, providerOptions: {
            "dreama-audio-async": { retainedOption: "kept", character_voices: [{ image_index: 1, name: "主角", voice_id: "voice-42" }] },
        } });
    });

    test("voice changes participate in duplicate request identity", () => {
        const first = context([image("person", catalog)]);
        const second = context([image("person", { ...catalog, voiceId: "voice-new" })]);
        const base = { nodeId: "target", mode: "video" as const, prompt: first.prompt, model: "same", options: {} };
        expect(canvasGenerationRequestFingerprint({ ...base, context: first })).not.toBe(canvasGenerationRequestFingerprint({ ...base, context: second }));
        const audio1 = prepareDreamaAudioIdentity(first, "audio");
        const audio2 = prepareDreamaAudioIdentity(second, "audio");
        expect(canvasGenerationRequestFingerprint({ ...base, mode: "audio", context: audio1 })).not.toBe(canvasGenerationRequestFingerprint({ ...base, mode: "audio", context: audio2 }));
    });
});
