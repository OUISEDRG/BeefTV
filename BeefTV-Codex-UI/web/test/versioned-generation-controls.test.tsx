import { expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { AudioSettingsPanel } from "@/components/audio-settings-panel";
import { ImageSettingsPanel } from "@/components/image-settings-panel";
import { VideoSettingsPanel } from "@/components/video-settings-panel";
import { CanvasVideoSettingsPopover } from "@/components/canvas/canvas-video-settings-popover";
import { CanvasImageSettingsPopover } from "@/components/canvas/canvas-image-settings-popover";
import { audioSettingsSummary, buildAudioSpeechRequest } from "@/lib/audio-generation";
import { canvasThemes } from "@/lib/canvas-theme";
import { defaultModelCapabilityConfig, modelCapabilityConfigFor } from "@/lib/model-capabilities";
import { createModelChannel, defaultConfig } from "@/stores/use-config-store";
import { generationPromptRequired } from "@/lib/generation-prompt";
import { mergeFetchedChannelModelProfiles, sanitizeChannelModelCatalogItem } from "@/lib/channel-model-catalog";
import { modelCompatibilityError } from "@/lib/model-selection";
import { resolveModelGenerationDefaults } from "@/lib/model-selection";
import { buildGenerationConfig, resolvedCanvasGenerationRequirements } from "@/lib/canvas/canvas-generation-config";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";
import { prepareBackendGenerationTask } from "@/services/api/generation-task";
import { prepareDreamaAudioIdentity, prepareDreamaVoiceProviderOptions } from "@/lib/canvas/dreama-voice-generation";

const audio = {
    voices: { supported: false, values: [], default: "" },
    formats: { supported: true, values: ["mp3"], default: "mp3" },
    speed: { supported: true, min: 0.8, max: 1.2, step: 0.1, default: 1 },
    pitch: { supported: false, min: 0, max: 0, step: 1, default: 0 },
    volume: { supported: false, min: 1, max: 1, step: 1, default: 1 },
    instructions: { supported: false }, references: { promptMaxChars: 1000 },
};
function configFor(capability: "audio" | "image" | "video", fields: Record<string, unknown>) {
    const channel = createModelChannel({ id: "declared", models: ["new-model"], modelProfiles: [{ model: "new-model", capability, protocol: capability === "audio" ? "openai-audio" : capability === "image" ? "openai-image" : "openai-videos", ...fields }] });
    return { ...defaultConfig, ...(capability === "audio" ? { audioVoice: "" } : {}), model: "declared::new-model", channels: [channel] };
}

test("versioned audio controls and request use the declared controls without OpenAI defaults", () => {
    const config = configFor("audio", { audioCapabilitiesVersion: "v1", capabilityConfig: { version: 1, audio } });
    const html = renderToStaticMarkup(<AudioSettingsPanel config={config} theme={canvasThemes.dark} onConfigChange={() => {}} />);
    expect(html).not.toContain("Alloy");
    expect(html).not.toContain("声音指令");
    expect(html).not.toContain("WAV");
    expect(html).toContain('min="0.8"');
    expect(audioSettingsSummary(config)).toBe("MP3 · 1x");
    expect(buildAudioSpeechRequest(config, "原文\n保留")).toEqual({ model: "declared::new-model", input: "原文\n保留", response_format: "mp3", speed: 1 });
});

test("arbitrary declared resolution tiers render without invented pixel dimensions", () => {
    const image = defaultModelCapabilityConfig().image!;
    image.size = { parameter: "aspect_ratio", values: ["16:9", "9:16"], default: "16:9", allowCustom: false };
    image.quality = { supported: true, values: ["2K", "3K", "6K"], default: "3K" };
    const config = { ...configFor("image", { imageCapabilitiesVersion: "v1", capabilityConfig: { version: 1, image } }), size: "16:9", quality: "3K" };
    const html = renderToStaticMarkup(<ImageSettingsPanel config={config} theme={canvasThemes.dark} onConfigChange={() => {}} />);
    expect(html).toContain(">3K</button>");
    expect(html).toContain(">6K</button>");
    expect(html).not.toContain("2752");
    expect(html).not.toContain(">4K</button>");
});

test("video operation semantics survive versioned capability lookup and display", () => {
    const video = { ...defaultModelCapabilityConfig().video!, operations: ["extend"], defaultOperation: "extend", promptRequired: true, durationSemantics: "additional", standaloneOutput: true, ratioSource: "project" };
    const config = configFor("video", { videoCapabilitiesVersion: "v1", capabilityConfig: { version: 1, video } });
    expect(modelCapabilityConfigFor(config, config.model).video).toMatchObject({ durationSemantics: "additional", standaloneOutput: true, ratioSource: "project" });
    const html = renderToStaticMarkup(<VideoSettingsPanel config={config} theme={canvasThemes.dark} onConfigChange={() => {}} />);
    expect(html).toContain("新增时长");
    expect(html).toContain("独立视频");
});

test("enhancement accepts an empty prompt only with an explicit capability", () => {
    const video = { ...defaultModelCapabilityConfig().video!, operations: ["upscale"], defaultOperation: "upscale", promptRequired: false };
    const config = configFor("video", { videoCapabilitiesVersion: "v1", capabilityConfig: { version: 1, video } });
    expect(generationPromptRequired(config, "video")).toBe(false);
    expect(generationPromptRequired(config, "image")).toBe(true);
    delete video.promptRequired;
    expect(generationPromptRequired(config, "video")).toBe(true);
});

test("single-operation models select their declared operation for reference-video inputs", () => {
    const video = { ...defaultModelCapabilityConfig().video!, operations: ["upscale"], defaultOperation: "upscale", promptRequired: false };
    video.references.maxVideos = 1;
    const config = configFor("video", { videoCapabilitiesVersion: "v1", capabilityConfig: { version: 1, video } });
    expect(modelCompatibilityError(config, config.model, { capability: "video", input: { imageCount: 0, videoCount: 1, audioCount: 0, characterCount: 0, textCount: 0 } })).toBe("");
});

test("audio capability survives the ordinary catalog merge and generation lookup", () => {
    const item = sanitizeChannelModelCatalogItem({ id: "new-model", modelType: "audio", audioCapabilitiesVersion: "v1", audioCapabilities: audio });
    expect(item).toMatchObject({ audioCapabilitiesVersion: "v1", audioCapabilities: audio });
    const config = configFor("audio", {});
    config.channels[0].modelProfiles = mergeFetchedChannelModelProfiles(config.channels[0], [item!]);
    expect(modelCapabilityConfigFor(config, config.model).audio).toEqual(audio);
});

test("selected resolution applies its individual duration and reference limits", () => {
    const video = { ...defaultModelCapabilityConfig().video!, resolutionVariants: { "1080p": { modelId: "variant", duration: { selection: "enum", values: [5], default: 5 }, references: { maxImages: 1, promptMaxChars: 500 } } } };
    const config = { ...configFor("video", { videoCapabilitiesVersion: "v1", capabilityConfig: { version: 1, video } }), vquality: "1080" };
    expect(modelCapabilityConfigFor(config, config.model).video).toMatchObject({ duration: { values: [5], default: 5 }, references: { maxImages: 1, promptMaxChars: 500 } });
    expect(resolveModelGenerationDefaults({ ...config, vquality: "720" }, config.model, "video", { vquality: "1080", videoSeconds: "10" }).videoSeconds).toBe("10");
    const node: CanvasNodeData = { id: "saved", type: CanvasNodeType.Video, title: "saved", position: { x: 0, y: 0 }, width: 100, height: 100, metadata: { model: config.model, vquality: "1080", seconds: "10" } };
    const resolved = buildGenerationConfig(config, node, "video");
    expect(resolved.videoSeconds).toBe("10");
    expect(modelCompatibilityError(resolved, resolved.model, resolvedCanvasGenerationRequirements(resolved, node, "video", { textCount: 1, imageCount: 0, videoCount: 0, audioCount: 0, characterCount: 0 }))).toContain("时长");
});

for (const engine of ["dreama-seed-audio-1.0", "dreama-voice-clone"]) {
    test(`${engine} accepts an explicitly empty default voice and waits for a selection`, () => {
        const capability = { ...audio, voices: { supported: true, values: [{ value: "101", label: "人物音色" }], default: "" } };
        const config = { ...configFor("audio", { audioCapabilitiesVersion: "v1", capabilityConfig: { version: 1, audio: capability } }), audioVoice: "" };
        config.channels[0].models = [engine];
        config.channels[0].modelProfiles![0].model = engine;
        config.model = `declared::${engine}`;
        const html = renderToStaticMarkup(<AudioSettingsPanel config={config} theme={canvasThemes.dark} onConfigChange={() => {}} />);
        expect(html).toContain("使用人物绑定或选择音色");
        expect(() => buildAudioSpeechRequest(config, "原文")).toThrow("音色");
        expect(buildAudioSpeechRequest({ ...config, audioVoice: "101" }, "原文")).toMatchObject({ voice: "101", input: "原文" });
    });
}

test("catalog refresh preserves removed audio voice and numeric choices and blocks submission", () => {
    const capability = { ...audio, voices: { supported: true, values: [{ value: "101", label: "人物音色" }], default: "101" } };
    const config = configFor("audio", { audioCapabilitiesVersion: "v1", capabilityConfig: { version: 1, audio: { ...capability, voices: { ...capability.voices, values: [...capability.voices.values, { value: "removed", label: "原音色" }] }, speed: { ...audio.speed, max: 3 } } } });
    const node: CanvasNodeData = { id: "saved", type: CanvasNodeType.Audio, title: "saved", position: { x: 0, y: 0 }, width: 100, height: 100, metadata: { model: config.model, audioVoice: "removed", audioSpeed: "3" } };
    expect(buildAudioSpeechRequest(buildGenerationConfig(config, node, "audio"), "原文")).toMatchObject({ voice: "removed", speed: 3 });
    const refreshed = sanitizeChannelModelCatalogItem({ id: "new-model", modelType: "audio", audioCapabilitiesVersion: "v2", audioCapabilities: capability });
    config.channels[0].modelProfiles = mergeFetchedChannelModelProfiles(config.channels[0], [refreshed!]);
    const resolved = buildGenerationConfig(config, node, "audio");
    expect(resolved).toMatchObject({ audioVoice: "removed", audioSpeed: "3" });
    expect(() => buildAudioSpeechRequest(resolved, "原文")).toThrow("音色");
    expect(() => buildAudioSpeechRequest({ ...resolved, audioVoice: "101" }, "原文")).toThrow("语速");
    expect(() => buildAudioSpeechRequest({ ...resolved, audioVoice: "101", audioSpeed: "1.05" }, "原文")).toThrow("语速");
    const html = renderToStaticMarkup(<AudioSettingsPanel config={resolved} theme={canvasThemes.dark} onConfigChange={() => {}} />);
    expect(html).toContain("removed");
    expect(html).toContain('value="3"');
    expect(html).toContain('role="alert"');
});

test("removed numeric controls retain explicit saved values and report the unsupported field", () => {
    const config = configFor("audio", { audioCapabilitiesVersion: "v2", capabilityConfig: { version: 1, audio } });
    const node: CanvasNodeData = { id: "saved", type: CanvasNodeType.Audio, title: "saved", position: { x: 0, y: 0 }, width: 100, height: 100, metadata: { model: config.model, audioPitch: "1.5", audioVolume: "1.2" } };
    const resolved = buildGenerationConfig(config, node, "audio");
    expect(resolved).toMatchObject({ audioPitch: "1.5", audioVolume: "1.2" });
    expect(() => buildAudioSpeechRequest(resolved, "原文")).toThrow("声调");
});

test("removed video resolution is preserved with a visible invalid-state message", () => {
    const video = { ...defaultModelCapabilityConfig().video!, resolutions: ["720p"], defaultResolution: "720p" };
    const config = configFor("video", { videoCapabilitiesVersion: "v2", capabilityConfig: { version: 1, video } });
    const node: CanvasNodeData = { id: "saved", type: CanvasNodeType.Video, title: "saved", position: { x: 0, y: 0 }, width: 100, height: 100, metadata: { model: config.model, vquality: "1080" } };
    const resolved = buildGenerationConfig(config, node, "video");
    expect(resolved.vquality).toBe("1080");
    const html = renderToStaticMarkup(<VideoSettingsPanel config={resolved} theme={canvasThemes.dark} onConfigChange={() => {}} />);
    expect(html).toContain('role="alert"');
    expect(html).toContain("分辨率「1080」");
    expect(renderToStaticMarkup(<CanvasVideoSettingsPopover config={resolved} onConfigChange={() => {}} />)).toContain("1080");
});

test("declared image choices remain visible and invalid after the catalog removes them", () => {
    const image = defaultModelCapabilityConfig().image!;
    image.size = { parameter: "aspect_ratio", values: ["16:9"], default: "16:9", allowCustom: false };
    image.quality = { supported: true, values: ["3K"], default: "3K" };
    const config = configFor("image", { imageCapabilitiesVersion: "v2", capabilityConfig: { version: 1, image } });
    const node: CanvasNodeData = { id: "saved", type: CanvasNodeType.Image, title: "saved", position: { x: 0, y: 0 }, width: 100, height: 100, metadata: { model: config.model, size: "9:16", quality: "6K" } };
    const resolved = buildGenerationConfig(config, node, "image");
    expect(resolved).toMatchObject({ size: "9:16", quality: "6K" });
    expect(modelCompatibilityError(resolved, resolved.model, { capability: "image", options: { size: resolved.size, quality: resolved.quality } })).toContain("9:16");
    const html = renderToStaticMarkup(<ImageSettingsPanel config={resolved} theme={canvasThemes.dark} onConfigChange={() => {}} />);
    expect(html).toContain('role="alert"');
    expect(html).toContain("9:16");
    const summary = renderToStaticMarkup(<CanvasImageSettingsPopover config={resolved} onConfigChange={() => {}} />);
    expect(summary).toContain("6K");
    expect(summary).toContain("9:16");
});

test("saved missing model identity never falls back to the global model", () => {
    const config = configFor("audio", { audioCapabilitiesVersion: "v1", capabilityConfig: { version: 1, audio } });
    const node: CanvasNodeData = { id: "saved", type: CanvasNodeType.Audio, title: "saved", position: { x: 0, y: 0 }, width: 100, height: 100, metadata: { model: "declared::removed" } };
    const resolved = buildGenerationConfig({ ...config, audioModel: config.model }, node, "audio");
    expect(resolved.model).toBe("declared::removed");
    expect(modelCompatibilityError(resolved, resolved.model, { capability: "audio" })).toContain("型号");
});

test("unset declared audio settings use capability defaults without borrowing global OpenAI voice", () => {
    const capability = { ...audio, voices: { supported: true, values: [{ value: "101", label: "人物音色" }], default: "" }, speed: { ...audio.speed, default: 1.1 } };
    const config = configFor("audio", { audioCapabilitiesVersion: "v1", capabilityConfig: { version: 1, audio: capability } });
    const node: CanvasNodeData = { id: "new", type: CanvasNodeType.Audio, title: "new", position: { x: 0, y: 0 }, width: 100, height: 100, metadata: { model: config.model } };
    expect(buildGenerationConfig(config, node, "audio")).toMatchObject({ audioVoice: "", audioSpeed: "1.1" });
});

test("task preparation rejects stale declared values before reading or uploading references", async () => {
    const config = { ...configFor("audio", { audioCapabilitiesVersion: "v1", capabilityConfig: { version: 1, audio } }), audioSpeed: "3" };
    await expect(prepareBackendGenerationTask({ mode: "audio", config, prompt: "原文", referenceImages: [{ id: "ref", name: "ref", type: "image/png", get dataUrl(): string { throw new Error("reference accessed before validation"); } }] })).rejects.toThrow("语速");
});

test("ordinary audio engine task accepts a bound voice while rejecting no selected voice", async () => {
    const capability = { ...audio, voices: { supported: true, values: [{ value: "101", label: "人物音色" }], default: "" } };
    const config = { ...configFor("audio", { protocol: "dreama-audio-async", audioCapabilitiesVersion: "v1", capabilityConfig: { version: 1, audio: capability } }), audioVoice: "" };
    await expect(prepareBackendGenerationTask({ mode: "audio", config, prompt: "原文" })).rejects.toThrow("音色");
    const context = prepareDreamaAudioIdentity({ referenceImages: [{ id: "person", name: "人物", type: "image/png", dataUrl: "", dreamaVoiceBinding: { version: 1 as const, source: "catalog" as const, voiceId: "101", name: "人物" } }], referenceAudios: [], characterReferences: [], imageCount: 1 }, "audio");
    const providerOptions = await prepareDreamaVoiceProviderOptions(context, config, "audio", async () => { throw new Error("catalog voice must not read sample"); });
    const task = await prepareBackendGenerationTask({ mode: "audio", config, prompt: " 原文\n", metadata: { providerOptions }, referenceImages: context.referenceImages });
    expect(task.input.prompt).toBe(" 原文\n");
    expect(task.input.metadata).toMatchObject({ providerOptions: { "dreama-audio-async": { character_voices: [{ voice_id: "101" }] } } });
    expect(task.input.config).toMatchObject({ audioVoice: "" });
});

test("declared pitch and volume retain their own ranges in controls and direct requests", () => {
    const capability = { ...audio, pitch: { supported: true, min: -2, max: 2, step: 0.5, default: 0 }, volume: { supported: true, min: 0, max: 2, step: 0.1, default: 1 } };
    const config = { ...configFor("audio", { audioCapabilitiesVersion: "v1", capabilityConfig: { version: 1, audio: capability } }), audioPitch: "1.5", audioVolume: "1.5" };
    expect(buildAudioSpeechRequest(config, "hello")).toMatchObject({ pitch: 1.5, volume: 1.5 });
    const html = renderToStaticMarkup(<AudioSettingsPanel config={config} theme={canvasThemes.dark} onConfigChange={() => {}} />);
    expect(html).toContain('min="-2"');
    expect(html).toContain('max="2"');
});
