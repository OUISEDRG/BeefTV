import { expect, test } from "bun:test";
import { normalizeCanvasMediaNodeSemantics } from "../src/lib/canvas/canvas-node-semantics";
import { buildImageGenerationMetadata } from "../src/lib/canvas/canvas-project-generation";
import { emotionGenerationSettings } from "../src/lib/canvas/canvas-local-image-edit";
import { defaultModelCapabilityConfig } from "../src/lib/model-capabilities";
import { createModelChannel, defaultConfig, encodeChannelModel } from "../src/stores/use-config-store";
import { CanvasNodeType, type CanvasNodeData, type CanvasNodeMetadata } from "../src/types/canvas";

const emotion: NonNullable<CanvasNodeMetadata["emotionEdit"]> = {
    sourceNodeId: "source", characterName: "角色1", presetId: "emotion-2-2", intimacy: 2, arousal: 2, label: "欣喜若狂",
    faceBox: { id: "face", x: 185, y: 205, width: 337, height: 337, source: "detected" },
    sourceWidth: 1774, sourceHeight: 887, providerSize: "1:1",
};
const node: CanvasNodeData = { id: "edited", type: CanvasNodeType.Image, title: "角色1", position: { x: 0, y: 0 }, width: 640, height: 320,
    metadata: { nodeRole: "result", resultOrigin: "generated", content: "/api/resources/full/file", storageKey: "resource:full",
        size: "1774x887", naturalWidth: 1774, naturalHeight: 887, emotionEdit: emotion } };

test("historical emotion nodes restore the frozen provider size while preserving the complete image", () => {
    const normalized = normalizeCanvasMediaNodeSemantics(node);
    expect(normalized.metadata?.size).toBe("1:1");
    expect({ ...normalized.metadata, size: "1774x887" }).toEqual(node.metadata);
    expect(node.metadata?.size).toBe("1774x887");
    expect(normalizeCanvasMediaNodeSemantics(normalized)).toBe(normalized);
});

test("emotion size recovery leaves user selections and missing frozen settings for normal validation", () => {
    for (const size of ["16:9", "invalid-user-choice"]) {
        const selected = { ...node, metadata: { ...node.metadata, size } };
        expect(normalizeCanvasMediaNodeSemantics(selected)).toBe(selected);
    }
    for (const emotionEdit of [undefined, { ...emotion, providerSize: undefined }]) {
        const unknown = { ...node, metadata: { ...node.metadata, emotionEdit } };
        expect(normalizeCanvasMediaNodeSemantics(unknown)).toBe(unknown);
    }
});

test("emotion generation metadata retains a provider capability size separate from source pixels", () => {
    const image = defaultModelCapabilityConfig("openai-image").image!;
    const channel = createModelChannel({ id: "fixture", enabled: true, interfaceType: "openai-image", models: ["editor"],
        modelProfiles: [{ model: "editor", capability: "image", protocol: "openai-image", capabilityConfig: { version: 1, image: {
            ...image, size: { ...image.size, parameter: "aspect_ratio", values: ["1:1", "16:9"], default: "1:1", allowCustom: false },
            references: { ...image.references, maxImages: 2 },
        } } }] });
    const model = encodeChannelModel(channel.id, "editor");
    const { generationConfig } = emotionGenerationSettings({ ...defaultConfig, channels: [channel], model, imageModel: model },
        { x: 0, y: 0, width: 809, height: 879 });
    const metadata = buildImageGenerationMetadata("edit", generationConfig, 1, []);
    expect(generationConfig.size).toBe("1:1");
    expect(metadata.size).toBe(generationConfig.size);
    expect(metadata.size).not.toBe(`${emotion.sourceWidth}x${emotion.sourceHeight}`);
});
