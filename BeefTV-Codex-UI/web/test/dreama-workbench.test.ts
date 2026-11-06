import { describe, expect, test } from "bun:test";
import { bindDreamaProject, freezeDreamaBinding, mergeDreamaProviderOptions, assertDreamaReferences, withoutDreamaBinding, buildDreamaGenerationPreview, dreamaCanvasVideoConfig } from "../src/lib/canvas/dreama-workbench";
import { createModelChannel, defaultConfig, encodeChannelModel } from "../src/stores/use-config-store";
import { defaultModelCapabilityConfig, modelCapabilityConfigFor } from "../src/lib/model-capabilities";
import { CanvasNodeType, type CanvasNodeData } from "../src/types/canvas";
import { useCanvasStore } from "../src/stores/canvas/use-canvas-store";
import { sameCanvasDocument } from "../src/lib/canvas/canvas-content";
import { rebaseCanvasDocumentThreeWay } from "../src/lib/canvas/canvas-document-rebase";

const project = { id: 41, name: "剧梦项目", promptMode: "drama", aspectRatio: "16:9" };
const node = (id: string, parentId?: string, seriesId?: number): CanvasNodeData => ({ id, parentId, type: seriesId ? CanvasNodeType.Frame : CanvasNodeType.Video, title: id, position: { x: 1, y: 2 }, width: 100, height: 100, metadata: seriesId ? { dreamaSeriesId: seriesId } : {} });
const binding = () => bindDreamaProject(undefined, project, 10);

test("canvas ratio overlay uses verified project ratio without modifying global catalog or saved conflicting choices", () => {
    const profile = { model: "video", protocol: "dreama-videos-async", capability: "video" as const, capabilityConfig: { version: 1 as const, video: { ...defaultModelCapabilityConfig("dreama-videos-async").video!, ratios: [], defaultRatio: "" } } };
    const channel = createModelChannel({ id: "dreama", interfaceType: "dreama-videos-async", models: ["video"], modelProfiles: [profile] });
    const config = { ...defaultConfig, channels: [channel], model: encodeChannelModel(channel.id, "video"), size: "9:16" };
    const result = dreamaCanvasVideoConfig(config, binding());
    expect(result.config.size).toBe("16:9");
    expect(modelCapabilityConfigFor(result.config, config.model).video?.ratios).toEqual(["16:9"]);
    expect(result.error).toBe("");
    expect(config.channels[0]?.modelProfiles?.[0]?.capabilityConfig?.video?.ratios).toEqual([]);
    const conflict = dreamaCanvasVideoConfig(config, binding(), "9:16");
    expect(conflict.config.size).toBe("9:16");
    expect(conflict.ratioConflict).toBe(true);
    expect(conflict.error).toContain("16:9");
    channel.modelProfiles![0]!.capabilityConfig!.video!.ratios = ["9:16"];
    expect(dreamaCanvasVideoConfig(config, binding()).error).toContain("不支持项目比例");
    const extension = structuredClone(config);
    extension.channels[0]!.modelProfiles![0]!.protocol = "dreama-extension-async";
    expect(dreamaCanvasVideoConfig(extension, binding(), "9:16")).toEqual({ config: extension, error: "", ratioConflict: false });
});

describe("Dreama canvas binding", () => {
    test("freezes actual group membership and keeps shared references independent", () => {
        const saved = binding();
        const nodes = [node("a", "episode-a"), node("b", "episode-b"), node("episode-a", undefined, 11), node("episode-b", undefined, 12), node("shared")];
        const a = freezeDreamaBinding(saved, "canvas", "a", nodes);
        const b = freezeDreamaBinding(saved, "canvas", "b", nodes);
        expect(a.seriesId).toBe(11);
        expect(b.seriesId).toBe(12);
        expect(freezeDreamaBinding(saved, "canvas", "shared", nodes).seriesId).toBe(10);
        nodes[0]!.parentId = "episode-b";
        saved.defaultSeriesId = 77;
        expect(a).toEqual({ schemaVersion: 1, canvasId: "canvas", nodeId: "a", projectId: 41, seriesId: 11, promptMode: "drama", aspectRatio: "16:9", bindingRevision: 1 });
    });
    test("rejects unknown canvases, rebinding, missing membership and contradictory nested groups", () => {
        expect(() => freezeDreamaBinding(undefined, "canvas", "a", [node("a")])).toThrow();
        expect(() => bindDreamaProject(binding(), { ...project, id: 42 }, 10)).toThrow();
        expect(() => bindDreamaProject(undefined, { ...project, promptMode: "storyboard" }, 10)).toThrow();
        expect(() => freezeDreamaBinding(binding(), "canvas", "a", [node("a", "missing")])).toThrow();
        expect(() => freezeDreamaBinding(binding(), "canvas", "a", [node("a", "inner"), node("inner", "outer", 11), node("outer", undefined, 12)])).toThrow();
    });
    test("copy for rebinding removes only integration routing and group routing", () => {
        const source = { integrations: { dreama: binding() }, nodes: [{ ...node("a", "group"), metadata: { prompt: "  原文\n原文  ", dreamaSeriesId: 10, dreamaVoiceBinding: { version: 1 as const, name: "角色", source: "catalog" as const, voiceId: "voice" } } }] };
        const copy = withoutDreamaBinding(source);
        expect(copy.integrations?.dreama).toBeUndefined();
        expect(copy.nodes[0]?.metadata?.dreamaSeriesId).toBeUndefined();
        expect(copy.nodes[0]?.metadata?.prompt).toBe("  原文\n原文  ");
        expect(copy.nodes[0]?.id).toBe("a");
        expect(source.nodes[0]?.metadata?.dreamaSeriesId).toBe(10);
    });
    test("document import and rebase retain binding alongside unrelated node changes", () => {
        const id = useCanvasStore.getState().importProject({ title: "test", nodes: [node("a")], integrations: { dreama: binding() } });
        const base = useCanvasStore.getState().openProject(id)!;
        expect(base.integrations?.dreama?.projectId).toBe(41);
        const local = { ...base, integrations: { dreama: { ...binding(), defaultSeriesId: 12, revision: 2 } } };
        const remote = { ...base, nodes: [{ ...node("a"), title: "外部修改" }], revision: 8 };
        expect(sameCanvasDocument(base, local)).toBe(false);
        const merged = rebaseCanvasDocumentThreeWay({ base, local, remote });
        expect(merged.conflict).toBe(false);
        expect(merged.project.integrations?.dreama?.defaultSeriesId).toBe(12);
        expect(merged.project.nodes[0]?.title).toBe("外部修改");
    });
});

test("final binding merges with voice namespace without mutating either snapshot", () => {
    const frozen = freezeDreamaBinding(binding(), "canvas", "a", [node("a")]);
    const voices = { "dreama-videos-async": { character_voices: [{ image_index: 2, name: "角色", voice_id: "v" }] } };
    const merged = mergeDreamaProviderOptions(frozen, voices);
    expect(merged.dreama.seriesId).toBe(10);
    expect(merged["dreama-videos-async"].character_voices).toEqual([{ image_index: 2, name: "角色", voice_id: "v" }]);
    merged["dreama-videos-async"].character_voices = [];
    expect(voices["dreama-videos-async"].character_voices).toHaveLength(1);
});

test("preflight blocks dangling, unused and over-limit media before upload", () => {
    const context = { prompt: "  @图片1\n@图片1  ", referenceImages: [{ id: "a" }], referenceVideos: [], referenceAudios: [] };
    expect(() => assertDreamaReferences(context, { maxImages: 1, maxVideos: 1, maxAudios: 1 }, "reference_to_video")).not.toThrow();
    expect(() => assertDreamaReferences({ ...context, prompt: "@视频2" }, undefined, "extend")).toThrow();
    expect(() => assertDreamaReferences({ ...context, referenceImages: [{ id: "a" }, { id: "b" }] }, undefined, "reference_to_video")).toThrow();
    expect(() => assertDreamaReferences(context, { maxImages: 0, maxVideos: 1, maxAudios: 1 }, "reference_to_video")).toThrow();
    expect(() => assertDreamaReferences({ ...context, prompt: "" }, undefined, "image_to_video")).not.toThrow();
    expect(context.prompt).toBe("  @图片1\n@图片1  ");
});

test("final preview keeps exact compiled text and voice indices without media secrets", () => {
    const preview = buildDreamaGenerationPreview(freezeDreamaBinding(binding(), "canvas", "a", [node("a")]), "model", "video", { quality: "3K" }, {
        prompt: "  @图片2 重复@图片2\n原文  ", referenceImages: [{ id: "first", name: "景" }, { id: "second", name: "人" }], referenceVideos: [], referenceAudios: [],
        dreamaVoiceProviderOptions: { "dreama-videos-async": { character_voices: [{ image_index: 2, name: "人", sampleResourceId: "owned-voice-sample" }] } },
    });
    expect(preview.prompt).toBe("  @图片2 重复@图片2\n原文  ");
    expect(preview.media).toEqual([{ kind: "图片", index: 1, id: "first", name: "景" }, { kind: "图片", index: 2, id: "second", name: "人" }]);
    expect(preview.voices).toEqual([{ image_index: 2, name: "人", sample: true }]);
    expect(JSON.stringify(preview)).not.toContain("base64");
});
