import { expect, test } from "bun:test";
import { canvasAIUpscaleAvailable, canvasStandaloneImageGenerationAvailable, canvasVideoOperationSupport, canvasWorkflowExecutionUnavailableReason, canvasVideoReversePromptAvailable } from "../src/lib/canvas/canvas-provider-capabilities";
import { availableCanvasWorkflowOperations } from "../src/lib/canvas/workflow-capabilities";
import { resolveAddNodeMenuCommands, type AddNodeMenuContext } from "../src/lib/canvas/tool-registry";
import { createModelChannel, defaultConfig, type AiConfig } from "../src/stores/use-config-store";
import { CanvasNodeType, type CanvasNodeData } from "../src/types/canvas";
import type { WorkflowOperation } from "../src/services/api/canvas-workflows";
import { generateDirectorPanorama } from "../src/lib/canvas/director/director-panorama-generation";

const dreama = createModelChannel({ id: "dreama", name: "Dreama", enabled: true, baseUrl: "http://127.0.0.1:18765", apiKey: "synthetic", models: ["image", "video", "extension"], modelProfiles: [
    { model: "image", protocol: "dreama-images-async", capability: "image" },
    { model: "video", protocol: "dreama-videos-async", capability: "video" },
    { model: "extension", protocol: "dreama-extension-async", capability: "video" },
] });
const codex = createModelChannel({ id: "codex", name: "Codex", enabled: true, baseUrl: "http://127.0.0.1:18766", apiKey: "synthetic", models: ["text"], modelProfiles: [{ model: "text", protocol: "openai-response", capability: "text" }] });
const config: AiConfig = { ...defaultConfig, channels: [dreama, codex], imageModel: "dreama::image", videoModel: "dreama::video", textModel: "codex::text", model: "dreama::image" };
const video: CanvasNodeData = { id: "video", type: CanvasNodeType.Video, title: "Video", position: { x: 0, y: 0 }, width: 320, height: 180, metadata: { model: "dreama::video", content: "resource:video" } };
const operation = (id: string, outputType: WorkflowOperation["outputType"] = "image", model?: string): WorkflowOperation => ({ id, name: id, version: 1, outputType, inputs: [], fields: model ? [{ key: "modelOption", label: "Model", type: "select", options: [{ value: model, label: model }] }] : [] });

test("video reverse prompting follows the installed adapter identity and selected text channel", () => {
    const local = createModelChannel({ id: "codex-local", enabled: true, interfaceType: "chat-completion", models: ["arbitrary-native-model"], modelProfiles: [{ model: "arbitrary-native-model", protocol: "chat-completion", capability: "text" }] });
    const external = createModelChannel({ id: "external", enabled: true, interfaceType: "chat-completion", models: ["arbitrary-native-model"], modelProfiles: [{ model: "arbitrary-native-model", protocol: "chat-completion", capability: "text" }] });
    const selected = { ...config, channels: [...config.channels, local, external], textModel: "codex-local::arbitrary-native-model" };
    expect(canvasVideoReversePromptAvailable(selected)).toBe(false);
    expect(canvasVideoReversePromptAvailable({ ...selected, textModel: "external::arbitrary-native-model" })).toBe(true);
    expect(canvasVideoReversePromptAvailable(config)).toBe(true);
});

test("Dreama video extension selects its configured adapter and unsupported edits disappear", () => {
    expect(canvasVideoOperationSupport(config, video, "extend")).toMatchObject({ available: true, model: "dreama::extension" });
    expect(canvasVideoOperationSupport(config, video, "inpaint").available).toBe(false);
    expect(canvasVideoOperationSupport(config, video, "draft_to_final").available).toBe(false);
    const noExtension = { ...config, channels: [{ ...dreama, models: ["video"] }, codex] };
    expect(canvasVideoOperationSupport(noExtension, video, "extend").available).toBe(false);
});

test("create menu preserves local tools and text while omitting unavailable upscale and placeholders", () => {
    const context = { config, workspaceMode: "professional", isProjectLinked: false, handlers: {} } as AddNodeMenuContext;
    const ids = resolveAddNodeMenuCommands(context).map(command => command.id);
    expect(ids).toContain("text");
    expect(ids).toContain("drawing");
    expect(ids).toContain("director");
    expect(ids).toContain(CanvasNodeType.BatchTable);
    expect(ids).not.toContain("upscale");
    expect(ids).not.toContain("script");
    expect(ids).not.toContain(CanvasNodeType.MediaConversion);
    expect(canvasAIUpscaleAvailable(config)).toBe(false);
    const withOfficial = { ...config, channels: [...config.channels, createModelChannel({ id: "beefapi", name: "Official", enabled: true, pinned: true, credentialRef: "beefapi-enterprise" })] };
    expect(canvasAIUpscaleAvailable(withOfficial)).toBe(true);
    expect(resolveAddNodeMenuCommands({ ...context, config: withOfficial }).map(command => command.id)).toContain("upscale");
});

test("workflow exposes Codex and local processing, excluding unbound Dreama media and official-only upscale", () => {
    const operations = [operation("generate.image", "image", "dreama::image"), operation("generate.text", "text", "codex::text"), operation("image.describe", "text", "codex::text"), operation("image.ai-upscale"), operation("video.depth", "video"), operation("video.trim", "video"), operation("image.crop")];
    expect(availableCanvasWorkflowOperations(operations, config).map(item => item.id)).toEqual(["generate.text", "image.describe", "video.depth", "video.trim", "image.crop"]);
    expect(availableCanvasWorkflowOperations([operation("generate.image")], config)).toEqual([]);
    expect(availableCanvasWorkflowOperations([{ ...operation("video.trim", "video"), unavailableReason: "Install encoder" }], config)[0].unavailableReason).toBe("Install encoder");
    expect(canvasWorkflowExecutionUnavailableReason(config, { version: 1, nodes: [{ id: "image", title: "Image", role: "operation", mediaType: "image", operationId: "generate.image", config: { modelOption: "dreama::image" } }], edges: [], outputNodeIds: ["image"] })).toContain("返回画布确认项目和剧集");
    expect(canvasWorkflowExecutionUnavailableReason(config, { version: 1, nodes: [{ id: "text", title: "Text", role: "operation", mediaType: "text", operationId: "generate.text", config: { modelOption: "codex::text" } }], edges: [], outputNodeIds: ["text"] })).toBe("");
});

test("standalone panorama rejects Dreama before upload or submission", async () => {
    expect(canvasStandaloneImageGenerationAvailable(config)).toBe(false);
    let uploads = 0, submits = 0;
    await expect(generateDirectorPanorama({ file: new File(["image"], "image.png", { type: "image/png" }), config, sceneId: "scene" }, {
        selectModel: () => "dreama::image", upload: async () => { uploads++; throw new Error("unexpected upload"); },
        submit: async () => { submits++; throw new Error("unexpected submit"); }, wait: async () => { throw new Error("unexpected wait"); },
        materialize: async () => { throw new Error("unexpected materialize"); }, findAsset: () => undefined, updateAsset: () => {},
    })).rejects.toThrow("尚未接入导演台全景生成");
    expect({ uploads, submits }).toEqual({ uploads: 0, submits: 0 });
});
