import { expect, test } from "bun:test";
import { canvasImageGenerationCountOptions, resolveCanvasImageGenerationCount } from "@/lib/canvas/canvas-image-generation-count";
import { buildGenerationConfig, canvasModelRequestOptions, preferredMediaGeneratorMetadata, resolvedCanvasGenerationRequirements } from "@/lib/canvas/canvas-generation-config";
import { defaultImageCapabilityConfig, normalizeImageValue } from "@/lib/model-capabilities";
import { modelCompatibilityError } from "@/lib/model-selection";
import { defaultConfig, type AiConfig } from "@/stores/use-config-store";
import { useCreationPreferencesStore } from "@/stores/use-creation-preferences-store";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";

const profile = { ...defaultImageCapabilityConfig(), fixedOutputs: 1, maxOutputs: 1, generationOutputs: [1] };
const config: AiConfig = {
    ...defaultConfig, imageModel: "single::image", model: "single::image", count: "7", canvasImageCount: "6",
    channels: [{ id: "single", name: "Single output", baseUrl: "https://example.test", apiKey: "fixture", apiFormat: "openai", models: ["image"],
        modelProfiles: [{ model: "image", capability: "image", protocol: "openai-image", capabilitySource: "user", capabilityConfig: { version: 1, image: profile } }],
    }],
};
const node: CanvasNodeData = { id: "image", type: CanvasNodeType.Image, title: "Image", position: { x: 0, y: 0 }, width: 100, height: 100, metadata: {} };

test("canvas totals permit one to eight while the single-request capability remains one", () => {
    expect(canvasImageGenerationCountOptions(profile)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    for (let count = 1; count <= 8; count++) {
        expect(resolveCanvasImageGenerationCount(profile, String(count))).toBe(count);
        expect(buildGenerationConfig(config, { ...node, metadata: { count } }, "image").count).toBe(String(count));
        expect(normalizeImageValue(profile, { count: String(count) }).count).toBe("1");
    }
});

test("canvas totals reject fractions and clamp invalid or out-of-range values", () => {
    for (const [value, expected] of [[undefined, 1], ["", 1], ["bad", 1], [Infinity, 1], [-5, 1], [0, 1], [4.9, 4], [99, 8]] as const) {
        expect(resolveCanvasImageGenerationCount(profile, value)).toBe(expected);
    }
});

test("native multi-output providers retain their original batch contract", () => {
    const four = { fixedOutputs: 4, generationOutputs: [4, 8, 12] };
    expect(canvasImageGenerationCountOptions(four)).toEqual([4, 8, 12]);
    expect(resolveCanvasImageGenerationCount(four, 12)).toBe(12);
    expect(resolveCanvasImageGenerationCount(four, 3)).toBe(4);
    expect(canvasImageGenerationCountOptions({ fixedOutputs: 4 })).toEqual([4]);
});

test("new nodes ignore old global count preferences while saved node counts survive round trips", () => {
    const previous = useCreationPreferencesStore.getState().preferences;
    try {
        useCreationPreferencesStore.setState({ preferences: { image: { count: "8" } } });
        expect(preferredMediaGeneratorMetadata(config, "image").count).toBe(1);
        expect(buildGenerationConfig(config, node, "image").count).toBe("1");
        const saved = JSON.parse(JSON.stringify({ ...node, metadata: { count: 5 } })) as CanvasNodeData;
        expect(buildGenerationConfig(config, saved, "image").count).toBe("5");
        expect(preferredMediaGeneratorMetadata(config, "image", { count: 3 }).count).toBe(3);
        useCreationPreferencesStore.setState({ preferences: {} });
        expect(preferredMediaGeneratorMetadata(config, "image").count).toBe(1);
    } finally {
        useCreationPreferencesStore.setState({ preferences: previous });
    }
});

test("logical single-output models admit repeated canvas tasks and native batches retain their request count", () => {
    const declared: AiConfig = { ...config, channels: config.channels.map(channel => ({ ...channel, modelProfiles: channel.modelProfiles?.map(model => ({
        ...model,
        logicalCapabilitySpec: { version: 1, capability: "image", options: { count: { values: [1] } } },
        capabilityConfig: { version: 1, image: { ...profile, fixedOutputs: undefined, generationOutputs: undefined } },
    })) })) };
    const resolved = buildGenerationConfig(declared, { ...node, metadata: { count: 4 } }, "image");
    const requirements = resolvedCanvasGenerationRequirements(resolved, node, "image", { textCount: 1, imageCount: 0, videoCount: 0, audioCount: 0, characterCount: 0 });
    expect(resolved.count).toBe("4");
    expect(requirements.options?.count).toBe(1);
    expect(modelCompatibilityError(resolved, resolved.model, requirements)).toBe("");
    const native: AiConfig = { ...config, count: "12", channels: config.channels.map(channel => ({ ...channel, modelProfiles: channel.modelProfiles?.map(model => ({
        ...model, capabilityConfig: { version: 1, image: { ...profile, fixedOutputs: 4, generationOutputs: [4, 8, 12] } },
    })) })) };
    expect(canvasModelRequestOptions(native, "image").count).toBe(4);
});
