import { expect, test } from "bun:test";
import { parseDreamaVoiceBinding, updateImageVoiceBinding, filterDreamaVoices, parseDreamaVoiceCatalog, dreamaVoiceCatalogChannel } from "@/lib/dreama-voice";
import { createModelChannel, defaultConfig } from "@/stores/use-config-store";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";

const binding = { version: 1 as const, name: "清朗男声", source: "catalog" as const, voiceId: "voice-123" };
const node: CanvasNodeData = { id: "picture", type: CanvasNodeType.Image, title: "image.png", position: { x: 10, y: 20 }, width: 300, height: 200, metadata: { content: "resource:image", storageKey: "resource:image", prompt: "原提示词\n保持原文", assetId: "image-asset" } };

test("binding saves and clears without changing the original picture, identity or prompt", () => {
    const sibling = { ...node, id: "other" };
    const original = structuredClone(node);
    const saved = updateImageVoiceBinding([node, sibling], "picture", binding);
    expect(saved[0].metadata?.dreamaVoiceBinding).toEqual(binding);
    expect(saved[0]).toEqual({ ...original, metadata: { ...original.metadata, dreamaVoiceBinding: binding } });
    expect(saved[1]).toBe(sibling);
    expect(node).toEqual(original);
    const reopened = JSON.parse(JSON.stringify(saved)) as CanvasNodeData[];
    expect(parseDreamaVoiceBinding(reopened[0].metadata?.dreamaVoiceBinding)).toEqual(binding);
    expect(updateImageVoiceBinding(reopened, "picture", undefined)[0]).toEqual(original);
});

test("malformed or ambiguous bindings cannot silently select a voice", () => {
    for (const value of [null, {}, { ...binding, version: 2 }, { ...binding, voiceId: "" }, { ...binding, source: "resource" }, { ...binding, sampleResourceId: "sample" }, { ...binding, name: "" }]) {
        expect(parseDreamaVoiceBinding(value)).toBeUndefined();
    }
    expect(parseDreamaVoiceBinding({ version: 1, source: "resource", name: "本机录音", sampleResourceId: "audio-123", previewUrl: "secret-url" })).toEqual({ version: 1, source: "resource", name: "本机录音", sampleResourceId: "audio-123" });
    expect(() => updateImageVoiceBinding([{ ...node, type: CanvasNodeType.Video }], "picture", binding)).toThrow();
    expect(() => updateImageVoiceBinding([node], "deleted", binding)).toThrow();
});

test("public and personal catalog can be searched without losing provider identity", () => {
    const items = parseDreamaVoiceCatalog({ voices: [{ id: "12", name: "温柔女声", source: "public", previewUrl: "https://media.example/12.mp3" }, { id: "23", name: "我的旁白", source: "personal", previewUrl: "https://media.example/23.wav" }] });
    expect(filterDreamaVoices(items, "旁白", "all").map(item => item.id)).toEqual(["23"]);
    expect(filterDreamaVoices(items, "", "public").map(item => item.id)).toEqual(["12"]);
    expect(() => parseDreamaVoiceCatalog({ voices: [{ id: "12", name: "x", source: "public", previewUrl: "javascript:alert(1)" }] })).toThrow();
});

test("catalog connection uses an enabled local audio protocol, not a similarly named remote model", () => {
    const channel = createModelChannel({ id: "configured-audio", enabled: true, name: "自己的命名", baseUrl: "http://127.0.0.1:18765/audio", interfaceType: "dreama-audio-async", apiKey: "test-key" });
    const remote = { ...channel, id: "remote", baseUrl: "https://example.com/audio" };
    expect(dreamaVoiceCatalogChannel({ ...defaultConfig, channels: [remote, channel] })?.id).toBe("configured-audio");
    expect(dreamaVoiceCatalogChannel({ ...defaultConfig, channels: [{ ...channel, enabled: false }] })).toBeUndefined();
});
