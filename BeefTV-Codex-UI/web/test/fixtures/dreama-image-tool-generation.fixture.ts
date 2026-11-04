import { expect, mock } from "bun:test";
import { createCanvasPageUiStore } from "../../src/stores/canvas/use-canvas-page-ui-store";
import { useCanvasStore } from "../../src/stores/canvas/use-canvas-store";
import { CanvasNodeType, type CanvasConnection, type CanvasNodeData } from "../../src/types/canvas";
import { defaultModelCapabilityConfig } from "../../src/lib/model-capabilities";

// Real tool handlers, prompt compiler and coordinator. Only hook hosting and
// paid/network execution boundaries are replaced, in an isolated process.
const react = await import("react");
const actualConfig = await import("../../src/stores/use-config-store");
const generation = await import("../../src/lib/canvas/canvas-project-generation");
const errors: string[] = [];
const dispatched: any[] = [];
let directDispatches = 0;
const no = () => {};
const imageProfile = { ...defaultModelCapabilityConfig("dreama-images-async").image!, references: { ...defaultModelCapabilityConfig("dreama-images-async").image!.references, supported: true, maxImages: 4, maskSupported: false } };
const channel = actualConfig.createModelChannel({ id: "dreama-image", name: "剧梦图片", enabled: true, baseUrl: "http://127.0.0.1:18765", apiKey: "test-only", interfaceType: "dreama-images-async", models: ["image"], modelProfiles: [{ model: "image", protocol: "dreama-images-async", capability: "image", imageCapabilitiesVersion: "test-v1", capabilityConfig: { version: 1, image: imageProfile } }] });
const model = actualConfig.encodeChannelModel(channel.id, "image");
const config = { ...actualConfig.defaultConfig, channels: [channel], imageModel: model, model, size: "1:1" };
mock.module("react", () => ({ ...react, useCallback: (fn: unknown) => fn, useRef: (value: unknown) => ({ current: value }), useEffect: no, useState: (value: unknown) => [value, no] }));
const message = { error: (text: string) => errors.push(text), warning: (text: string) => errors.push(text), loading: no, destroy: no, info: no, success: no };
mock.module("antd", () => ({ App: { useApp: () => ({ modal: { confirm: () => { throw new Error("unexpected confirmation"); } }, message }) } }));
mock.module("@/stores/use-config-store", () => ({ ...actualConfig, useEffectiveConfig: () => config, useConfigStore: Object.assign((selector: any) => selector({ config, isAiConfigReady: () => true }), { getState: () => ({ config }) }) }));
mock.module("@/components/generation-reference-links", () => ({ createReferenceLinkResolver: () => undefined }));
mock.module("@/components/canvas/canvas-node-lighting-dialog", () => ({ buildLightingLabel: () => "重新打光" }));
mock.module("@/pages/canvas/canvas-image-generation-executor", () => ({ executeImageGeneration: async (value: unknown) => { dispatched.push(value); } }));
mock.module("@/lib/canvas/canvas-project-generation", () => ({ ...generation, runBackendCanvasGenerationTask: async () => { directDispatches++; throw new Error("direct executor bypass"); } }));
const { useCanvasGenerationExecutor } = await import("../../src/pages/canvas/use-canvas-generation-executor");
const { useCanvasImageTools } = await import("../../src/pages/canvas/use-canvas-image-tools");
const { useCanvasGenerationRetry } = await import("../../src/pages/canvas/use-canvas-generation-retry");
const imageToolHelpers = await import("../../src/lib/canvas/image-tool-generation");
const { updateImageToolPortraitTexture } = imageToolHelpers;

function setup() {
    errors.length = 0;
    dispatched.length = 0;
    directDispatches = 0;
    const base = { position: { x: 0, y: 0 }, width: 100, height: 100 };
    const source: CanvasNodeData = { ...base, id: "original", type: CanvasNodeType.Image, title: "人物", parentId: "episode", metadata: { content: "/api/resources/original/file", storageKey: "resource:original", model, size: "1:1", dreamaVoiceBinding: { version: 1, source: "catalog", name: "甲", voiceId: "voice-1" } } };
    const nodes: CanvasNodeData[] = [source, { ...base, id: "episode", type: CanvasNodeType.Frame, title: "第二集", metadata: { dreamaSeriesId: 22 } }];
    const projectId = useCanvasStore.getState().importProject({ title: "image tools", nodes, integrations: { dreama: { schemaVersion: 1, projectId: 41, projectName: "剧", promptMode: "drama", aspectRatio: "16:9", defaultSeriesId: 11, revision: 3 } } });
    const nodesRef = { current: nodes };
    const connectionsRef = { current: [] as CanvasConnection[] };
    // A React setState call does not update refs synchronously. The tool adapter
    // must publish its child and edge itself before invoking the coordinator.
    let renderedNodes = nodes;
    let renderedConnections: CanvasConnection[] = [];
    const setNodes = (value: any) => { renderedNodes = typeof value === "function" ? value(renderedNodes) : value; };
    const setConnections = (value: any) => { renderedConnections = typeof value === "function" ? value(renderedConnections) : value; };
    const commitReactState = () => { nodesRef.current = renderedNodes; connectionsRef.current = renderedConnections; };
    const common = { projectId, pageUi: createCanvasPageUiStore(), nodesRef, connectionsRef, setNodes, setConnections, setRunningNodeId: no, startGenerationRequest: () => new AbortController(), finishGenerationRequest: no, bindGenerationTask: no };
    const generate = useCanvasGenerationExecutor({ ...common, assets: [], addedSkills: [], applyGenerationTaskResult: async () => {} });
    const tools = useCanvasImageTools({ ...common, handleGenerateNode: generate, message: message as any, beginMediaOperation: () => ({ isCurrent: () => true, wait: (value: any) => value, run: (fn: any) => fn() }) as any, persistMediaNodes: async () => new Map() });
    const retry = useCanvasGenerationRetry({ ...common, assets: [], addedSkills: [], applyGenerationTaskResult: async () => {}, regenerateImageBatch: async (nodeId, prompt) => { await generate(nodeId, "image", prompt); } });
    return { source, projectId, nodesRef, connectionsRef, tools, generate, retry, commitReactState };
}

{
    const local = actualConfig.createModelChannel({ id: "codex-local", enabled: true, interfaceType: "chat-completion", models: ["text-test"], modelProfiles: [{ model: "text-test", capability: "text", protocol: "chat-completion" }] });
    config.channels = [...config.channels, local];
    config.textModel = actualConfig.encodeChannelModel(local.id, "text-test");
    const { source, nodesRef, connectionsRef, tools, commitReactState } = setup();
    tools.createImageReversePromptNodes({ ...source, type: CanvasNodeType.Video });
    commitReactState();
    expect(nodesRef.current).toHaveLength(2);
    expect(connectionsRef.current).toHaveLength(0);
    expect(errors.at(-1)).toContain("不支持视频反推");
    tools.createImageReversePromptNodes(source);
    commitReactState();
    expect(nodesRef.current).toHaveLength(4);
    expect(connectionsRef.current).toHaveLength(2);
    expect(nodesRef.current.at(-1)?.metadata?.model).toBe("codex-local::text-test");
    expect(directDispatches).toBe(0);
    expect(dispatched).toHaveLength(0);
    config.channels = config.channels.filter(item => item.id !== local.id);
    config.textModel = actualConfig.defaultConfig.textModel;
    console.log("PASS: Codex video reverse handler blocks before node writes and image reverse stays available");
}

{
    const { source, projectId, nodesRef, tools } = setup();
    const pending = tools.generateAngleNode(source, { horizontalAngle: 45, pitchAngle: 0, cameraDistance: 4.8, wideAngle: false });
    const canvas = useCanvasStore.getState().openProject(projectId)!;
    useCanvasStore.getState().updateProject(projectId, { integrations: { dreama: { ...canvas.integrations!.dreama!, defaultSeriesId: 66, revision: 4 } } });
    nodesRef.current = nodesRef.current.map(node => node.id === "episode" ? { ...node, metadata: { dreamaSeriesId: 77 } } : node);
    await pending;
    expect(directDispatches).toBe(0);
    expect(errors).toEqual([]);
    expect(dispatched).toHaveLength(1);
    expect(dispatched[0].sourceNode.parentId).toBe("episode");
    expect(dispatched[0].sourceNode.metadata.composerContent).toContain("@[node:original]");
    expect(dispatched[0].effectivePrompt).toContain("@图片1");
    expect(dispatched[0].generationContext.referenceImages.map((image: any) => image.id)).toEqual(["original"]);
    expect(dispatched[0].generationContext.dreamaVoiceProviderOptions).toEqual({ dreama: { schemaVersion: 1, canvasId: projectId, nodeId: dispatched[0].nodeId, projectId: 41, seriesId: 22, promptMode: "drama", aspectRatio: "16:9", bindingRevision: 3 } });
    expect(source.metadata?.dreamaVoiceBinding?.voiceId).toBe("voice-1");
    console.log("PASS: angle uses the coordinator and freezes the source episode before asynchronous preparation");
}

{
    for (const kind of ["lighting", "portraitTexture"] as const) {
        const { source, nodesRef, tools, generate, retry } = setup();
        if (kind === "lighting") tools.generateLightingNode(source, {} as any, "  让左侧光线更柔和。\n保留色调。  ");
        else tools.openPortraitTextureEditor(source);
        expect(dispatched).toHaveLength(0);
        let draft = nodesRef.current.at(-1)!;
        expect(draft.parentId).toBe("episode");
        expect(draft.metadata?.composerContent).toContain("@[node:original]");
        expect(draft.metadata?.portraitTexture).toBeUndefined();
        if (kind === "portraitTexture") {
            const patch = updateImageToolPortraitTexture(draft, draft.metadata!.composerContent! + "\n保留我添加的围巾细节。", { skin: "real" });
            draft = { ...draft, metadata: { ...draft.metadata, ...patch } };
            nodesRef.current = nodesRef.current.map(node => node.id === draft.id ? draft : node);
            expect(draft.metadata!.composerContent).toContain("强化真实皮肤肌理和细微质感，避免塑料感");
            expect(draft.metadata!.composerContent).not.toContain("保留自然肤质、毛孔和真实肤色过渡");
            expect(draft.metadata!.composerContent).toEndWith("保留我添加的围巾细节。");
        }
        const authored = draft.metadata!.composerContent!;
        await generate(draft.id, "image", authored);
        expect(errors).toEqual([]);
        expect(directDispatches).toBe(0);
        expect(dispatched).toHaveLength(1);
        expect(dispatched[0].effectivePrompt).toBe(authored.replace("@[node:original]", "@图片1"));
        expect(dispatched[0].generationContext.dreamaVoiceProviderOptions.dreama.seriesId).toBe(22);
        await retry(draft);
        expect(dispatched).toHaveLength(2);
        expect(dispatched[1].effectivePrompt).toBe(dispatched[0].effectivePrompt);
        expect(dispatched[1].generationContext.dreamaVoiceProviderOptions.dreama.seriesId).toBe(22);
        expect(directDispatches).toBe(0);
    }
    console.log("PASS: lighting and portrait drafts submit their visible authored prompts without hidden additions");
}

{
    const { source, nodesRef, tools, generate } = setup();
    await tools.maskEditImageNode(source, { prompt: "编辑", maskDataUrl: "data:image/png;base64,AA==" });
    await tools.generateEmotionNode(source, {} as any);
    expect(nodesRef.current).toHaveLength(2);
    expect(directDispatches).toBe(0);
    expect(errors).toHaveLength(2);
    tools.openPortraitTextureEditor(source);
    const draft = nodesRef.current.at(-1)!;
    for (const patch of [{ emotionEdit: { sourceNodeId: source.id } }, { portraitTexture: { skin: "real" } }, { imageTool: { kind: "maskEdit", sourceNodeId: source.id } }]) {
        nodesRef.current = nodesRef.current.map(node => node.id === draft.id ? { ...draft, metadata: { ...draft.metadata, ...patch } } as any : node);
        await generate(draft.id, "image", draft.metadata!.composerContent!);
        expect(dispatched).toHaveLength(0);
    }
    const legacy = { ...source, metadata: { ...source.metadata, portraitTexture: { skin: "real" }, composerContent: "保留旧节点正文" } } as CanvasNodeData;
    expect(imageToolHelpers.dreamaImageToolPromptError(legacy)).toContain("从原图重新打开");
    expect(legacy.metadata?.composerContent).toBe("保留旧节点正文");
    console.log("PASS: unsupported direct edits and stale tool metadata cannot dispatch");
}

{
    const { source, nodesRef, connectionsRef, tools, generate } = setup();
    tools.openPortraitTextureEditor(source);
    const draft = nodesRef.current.at(-1)!;
    await generate(draft.id, "image", "完全省略原图引用");
    expect(dispatched).toHaveLength(0);
    nodesRef.current.push({ ...source, id: "other", metadata: { storageKey: "resource:other", content: "/api/resources/other/file" } });
    connectionsRef.current.push({ id: "other", fromNodeId: "other", toNodeId: draft.id });
    await generate(draft.id, "image", draft.metadata!.composerContent!);
    expect(dispatched).toHaveLength(0);
    connectionsRef.current = connectionsRef.current.filter(connection => connection.fromNodeId !== "other");
    nodesRef.current = nodesRef.current.filter(node => node.id !== source.id);
    await generate(draft.id, "image", draft.metadata!.composerContent!);
    expect(dispatched).toHaveLength(0);
    console.log("PASS: missing or extra references stop before dispatch");
}

{
    channel.interfaceType = "openai-image";
    channel.modelProfiles![0]!.protocol = "openai-image";
    const { source, nodesRef, tools, commitReactState } = setup();
    tools.openPortraitTextureEditor(source);
    commitReactState();
    const portrait = nodesRef.current.at(-1)!;
    expect(portrait.metadata?.portraitTexture).toBeDefined();
    expect(portrait.metadata?.imageTool).toBeUndefined();
    expect(portrait.metadata?.composerContent).toBe("@图片1");
    await tools.generateAngleNode(source, { horizontalAngle: 45, pitchAngle: 0, cameraDistance: 4.8, wideAngle: false });
    expect(directDispatches).toBe(1);
    expect(dispatched).toHaveLength(0);
    console.log("PASS: non-Dreama tools retain their existing execution and texture controls");
}
