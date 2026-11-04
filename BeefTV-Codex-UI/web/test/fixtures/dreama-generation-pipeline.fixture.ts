import { expect, mock, spyOn } from "bun:test";
import { createCanvasPageUiStore } from "../../src/stores/canvas/use-canvas-page-ui-store";
import { useCanvasStore } from "../../src/stores/canvas/use-canvas-store";
import { CanvasNodeType, type CanvasNodeData } from "../../src/types/canvas";
import { defaultModelCapabilityConfig } from "../../src/lib/model-capabilities";

// Exercise the real compiler and coordinator; replace only React hook hosting,
// app notifications and final paid executors at their explicit boundaries.
// This fixture is launched in its own process so mocks cannot affect other tests.
const react = await import("react");
const actualConfig = await import("../../src/stores/use-config-store");
const errors: string[] = [];
const dispatched: any[] = [];
const no = () => {};
const channel = actualConfig.createModelChannel({ id: "dreama", name: "剧梦", enabled: true, baseUrl: "http://127.0.0.1:18765", apiKey: "test-only", interfaceType: "dreama-videos-async", models: ["video"], modelProfiles: [{ model: "video", protocol: "dreama-videos-async", capability: "video", videoCapabilitiesVersion: "test-v1", capabilityConfig: { version: 1, video: { ...defaultModelCapabilityConfig("dreama-videos-async").video!, operations: ["reference_to_video"], references: { maxImages: 9, maxVideos: 1, maxAudios: 0 } } } }] });
const model = actualConfig.encodeChannelModel(channel.id, "video");
const config = { ...actualConfig.defaultConfig, channels: [channel], videoModel: model, model, videoSeconds: "5", vquality: "720" };
mock.module("react", () => ({ ...react, useCallback: (fn: unknown) => fn, useRef: (value: unknown) => ({ current: value }) }));
mock.module("antd", () => ({ App: { useApp: () => ({ modal: { confirm: () => { throw new Error("unexpected confirmation"); } }, message: { error: (text: string) => errors.push(text), loading: no, destroy: no, info: no, warning: no } }) } }));
mock.module("@/stores/use-config-store", () => ({ ...actualConfig, useEffectiveConfig: () => config, useConfigStore: Object.assign((selector: any) => selector({ config, isAiConfigReady: () => true }), { getState: () => ({ config }) }) }));
mock.module("@/components/generation-reference-links", () => ({ createReferenceLinkResolver: () => undefined }));
mock.module("@/pages/canvas/canvas-media-generation-executors", () => ({ executeVideoGeneration: async (value: unknown) => { dispatched.push(value); }, executeAudioGeneration: async (value: unknown) => { dispatched.push(value); } }));
mock.module("@/pages/canvas/canvas-image-generation-executor", () => ({ executeImageGeneration: async (value: unknown) => { dispatched.push(value); } }));
const { useCanvasGenerationExecutor } = await import("../../src/pages/canvas/use-canvas-generation-executor");
async function check(name: string, run: () => Promise<void>) {
    errors.length = 0;
    dispatched.length = 0;
    await run();
    console.log(`PASS: ${name}`);
}

await check("real character hydration expands only markers while ordinary speaker voice follows final indices", async () => {
    const projects = await import("../../src/services/api/projects");
    const read = spyOn(projects, "getProjectCharacter").mockResolvedValue({ asset: { id: "role-asset", title: "甲" }, character: { versionId: "v1", definition: { appearance: "不能插入的角色设定" }, representations: [{ id: "front", resourceId: "front-image", role: "primary" }, { id: "side", resourceId: "side-image", role: "side" }] } } as any);
    try {
        const base = { position: { x: 0, y: 0 }, width: 100, height: 100 };
        const nodes: CanvasNodeData[] = [
            { ...base, id: "role", type: CanvasNodeType.Text, title: "甲", metadata: { workflowKind: "character", characterAssetId: "role-asset", content: "不能插入的角色正文" } },
            { ...base, id: "voice", type: CanvasNodeType.Image, title: "乙", metadata: { storageKey: "resource:speaker", content: "/api/resources/speaker/file", dreamaVoiceBinding: { version: 1, name: "乙", source: "catalog", voiceId: "voice-b" } } },
            { ...base, id: "target", type: CanvasNodeType.Video, title: "生成", metadata: { model, videoEditOperation: "reference_to_video" } },
        ];
        const canvasId = useCanvasStore.getState().importProject({ title: "角色", nodes, integrations: { dreama: { schemaVersion: 1, projectId: 41, projectName: "剧", promptMode: "drama", aspectRatio: "16:9", defaultSeriesId: 11, revision: 1 } } });
        const nodesRef = { current: nodes };
        const generate = useCanvasGenerationExecutor({ projectId: canvasId, domainProjectId: "local-project", pageUi: createCanvasPageUiStore(), assets: [], addedSkills: [], nodesRef,
            connectionsRef: { current: [{ id: "role", fromNodeId: "role", toNodeId: "target" }, { id: "voice", fromNodeId: "voice", toNodeId: "target" }] }, setNodes: value => { nodesRef.current = typeof value === "function" ? value(nodesRef.current) : value; }, setConnections: no, setRunningNodeId: no,
            startGenerationRequest: () => new AbortController(), finishGenerationRequest: no, bindGenerationTask: no, applyGenerationTaskResult: async () => {},
        });
        await generate("target", "video", "  @[node:role] 与 @[node:voice]。\n@[node:role] 再说。  ", { previewOnly: true });
        expect(errors).toEqual([]);
        const preview = nodesRef.current[2]?.metadata?.dreamaGenerationPreview;
        expect(preview?.prompt).toBe("  @图片1、@图片2 与 @图片3。\n@图片1、@图片2 再说。  ");
        expect(preview?.voices).toEqual([{ image_index: 3, name: "乙", voice_id: "voice-b" }]);
        expect(dispatched).toHaveLength(0);
    } finally { read.mockRestore(); }
});

await check("Dreama image and audio coordinator preserve authored text instead of appending connected text", async () => {
    for (const mode of ["image", "audio"] as const) {
        const protocol = mode === "image" ? "dreama-images-async" : "dreama-audio-async";
        const extra = actualConfig.createModelChannel({ id: mode, name: mode, enabled: true, baseUrl: "http://127.0.0.1:18765", apiKey: "test-only", interfaceType: protocol, models: [mode], modelProfiles: [{ model: mode, capability: mode, protocol }] });
        const key = actualConfig.encodeChannelModel(extra.id, mode);
        config.channels = [...config.channels.filter(value => value.id !== mode), extra];
        config[mode === "image" ? "imageModel" : "audioModel"] = key;
        const base = { position: { x: 0, y: 0 }, width: 100, height: 100 };
        const nodes: CanvasNodeData[] = [{ ...base, id: "note", type: CanvasNodeType.Text, title: "上游", metadata: { content: "自动附上的上游文本" } }, { ...base, id: "target", type: mode === "image" ? CanvasNodeType.Image : CanvasNodeType.Audio, title: "生成", metadata: { model: key, size: "1:1" } }];
        const canvasId = useCanvasStore.getState().importProject({ title: mode, nodes, integrations: { dreama: { schemaVersion: 1, projectId: 41, projectName: "剧", promptMode: "drama", aspectRatio: "16:9", defaultSeriesId: 11, revision: 1 } } });
        const nodesRef = { current: nodes };
        const generate = useCanvasGenerationExecutor({ projectId: canvasId, pageUi: createCanvasPageUiStore(), assets: [], addedSkills: [{ skillId: "inactive", skillName: "未启用技能", isAdded: false } as any], nodesRef,
            connectionsRef: { current: [{ id: "text", fromNodeId: "note", toNodeId: "target" }] }, setNodes: value => { nodesRef.current = typeof value === "function" ? value(nodesRef.current) : value; }, setConnections: no, setRunningNodeId: no,
            startGenerationRequest: () => new AbortController(), finishGenerationRequest: no, bindGenerationTask: no, applyGenerationTaskResult: async () => {},
        });
        await generate("target", mode, "  正文\n 保留  ", { previewOnly: true });
        expect(errors).toEqual([]);
        expect(nodesRef.current[1]?.metadata?.dreamaGenerationPreview?.prompt).toBe("  正文\n 保留  ");
        if (mode === "image") expect(nodesRef.current[1]?.metadata?.dreamaGenerationPreview?.parameters.size).toBe("1:1");
        await generate("target", mode, "  开始@[node:note]结束  ", { previewOnly: true });
        expect(nodesRef.current[1]?.metadata?.dreamaGenerationPreview?.prompt).toBe("  开始自动附上的上游文本结束  ");
        await generate("target", mode, "  原样保留 @[skill:inactive]  ", { previewOnly: true });
        expect(nodesRef.current[1]?.metadata?.dreamaGenerationPreview?.prompt).toBe("  原样保留 @[skill:inactive]  ");
        if (mode === "image") {
            nodesRef.current[1]!.metadata!.cameraControl = { enabled: true } as any;
            await generate("target", mode, "  正文  ", { previewOnly: true });
            expect(errors.pop()).toContain("不自动添加摄影");
        }
    }
});

await check("real canvas pipeline previews without dispatch, freezes group binding and merges compiled voice indices", async () => {
    const base = { position: { x: 0, y: 0 }, width: 100, height: 100 };
    const nodes: CanvasNodeData[] = [
        { ...base, id: "image-a", type: CanvasNodeType.Image, title: "场景", metadata: { storageKey: "resource:a", content: "/api/resources/a/file" } },
        { ...base, id: "image-b", type: CanvasNodeType.Image, title: "主角", metadata: { storageKey: "resource:b", content: "/api/resources/b/file", dreamaVoiceBinding: { version: 1, name: "主角", source: "catalog", voiceId: "voice-2" } } },
        { ...base, id: "episode", type: CanvasNodeType.Frame, title: "第二集", metadata: { dreamaSeriesId: 12 } },
        { ...base, id: "target", type: CanvasNodeType.Video, title: "镜1", parentId: "episode", metadata: { model, videoEditOperation: "reference_to_video" } },
    ];
    const canvasId = useCanvasStore.getState().importProject({ title: "pipeline", nodes, integrations: { dreama: { schemaVersion: 1, projectId: 41, projectName: "剧", promptMode: "drama", aspectRatio: "16:9", defaultSeriesId: 11, revision: 3 } } });
    const nodesRef = { current: nodes };
    const generate = useCanvasGenerationExecutor({ projectId: canvasId, pageUi: createCanvasPageUiStore(), assets: [], addedSkills: [], nodesRef,
        connectionsRef: { current: [{ id: "ea", fromNodeId: "image-a", toNodeId: "target" }, { id: "eb", fromNodeId: "image-b", toNodeId: "target" }] },
        setNodes: value => { nodesRef.current = typeof value === "function" ? value(nodesRef.current) : value; }, setConnections: no, setRunningNodeId: no,
        startGenerationRequest: () => new AbortController(), finishGenerationRequest: no, bindGenerationTask: no, applyGenerationTaskResult: async () => {},
    });
    const prompt = "  @[node:image-b] 说话，@[node:image-a] 是背景。\n@[node:image-b] 再说一句。  ";
    await generate("target", "video", prompt, { previewOnly: true });
    expect(errors).toEqual([]);
    expect(dispatched).toHaveLength(0);
    const preview = nodesRef.current.find(node => node.id === "target")?.metadata?.dreamaGenerationPreview;
    expect(preview?.binding.seriesId).toBe(12);
    expect(preview?.prompt).toBe("  @图片1 说话，@图片2 是背景。\n@图片1 再说一句。  ");
    expect(preview?.voices).toEqual([{ image_index: 1, name: "主角", voice_id: "voice-2" }]);
    const pending = generate("target", "video", prompt);
    const canvas = useCanvasStore.getState().openProject(canvasId)!;
    useCanvasStore.getState().updateProject(canvasId, { integrations: { dreama: { ...canvas.integrations!.dreama!, defaultSeriesId: 77, revision: 4 } } });
    nodesRef.current = nodesRef.current.map(node => node.id === "episode" ? { ...node, metadata: { dreamaSeriesId: 88 } } : node);
    await pending;
    expect(errors).toEqual([]);
    expect(dispatched).toHaveLength(1);
    expect(dispatched[0].generationContext.dreamaVoiceProviderOptions).toMatchObject({ dreama: { projectId: 41, seriesId: 12, bindingRevision: 3 }, "dreama-videos-async": { character_voices: [{ image_index: 1, name: "主角", voice_id: "voice-2" }] } });
    expect(dispatched[0].effectivePrompt).toBe(preview?.prompt);
    expect(dispatched[0].generationConfig.size).toBe("16:9");
    nodesRef.current = nodesRef.current.map(node => ({ ...node, metadata: { ...node.metadata, status: "idle" } }));
    await generate("target", "video", "@[node:image-b]");
    expect(dispatched).toHaveLength(1);
    expect(errors.at(-1)).toContain("尚未在提示词中引用");
    useCanvasStore.getState().updateProject(canvasId, { integrations: undefined });
    await generate("target", "video", prompt);
    expect(dispatched).toHaveLength(1);
    expect(errors.at(-1)).toContain("绑定此画布");
    useCanvasStore.getState().updateProject(canvasId, { integrations: canvas.integrations });
    nodesRef.current = nodesRef.current.map(node => node.id === "target" ? { ...node, metadata: { ...node.metadata, size: "9:16" } } : node);
    await generate("target", "video", prompt, { previewOnly: true });
    expect(dispatched).toHaveLength(1);
    expect(errors.at(-1)).toContain("与项目比例 16:9 不一致");
});
