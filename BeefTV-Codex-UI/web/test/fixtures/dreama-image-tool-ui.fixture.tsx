import { expect, mock } from "bun:test";
import type { ReactElement } from "react";
import { createModelChannel, defaultConfig, encodeChannelModel } from "../../src/stores/use-config-store";
import { defaultModelCapabilityConfig } from "../../src/lib/model-capabilities";
import { CanvasNodeType, type CanvasNodeData } from "../../src/types/canvas";

// A local hook host exercises changes to an already-open editor. Only rendering
// state is replaced; model selection, support checks and event handlers are real.
const react = await import("react");
let cursor = 0;
let state: unknown[] = [];
mock.module("react", () => ({ ...react,
    useState: (initial: unknown) => {
        const index = cursor++;
        if (!(index in state)) state[index] = typeof initial === "function" ? initial() : initial;
        return [state[index], (value: unknown) => { state[index] = typeof value === "function" ? value(state[index]) : value; }];
    },
    useRef: (current: unknown) => ({ current }), useEffect: () => {}, useLayoutEffect: () => {}, useId: () => "support-notice",
    useMemo: (compute: () => unknown) => compute(), useCallback: (callback: unknown) => callback,
    useSyncExternalStore: (_subscribe: unknown, snapshot: () => unknown) => snapshot(),
}));
mock.module("@/stores/canvas/use-canvas-theme-store", () => ({ useActiveTheme: () => "dark" }));
const { CanvasImageMaskEditor } = await import("../../src/components/canvas/canvas-node-mask-edit-dialog");
const { NodeDockMenuButton } = await import("../../src/components/canvas/canvas-node-dock-tools");

function elements(value: unknown): ReactElement<any>[] {
    if (Array.isArray(value)) return value.flatMap(elements);
    if (!react.isValidElement(value)) return [];
    const element = value as ReactElement<any>;
    return [element, ...elements(element.props.children)];
}

const image = defaultModelCapabilityConfig("openai-image").image!;
const openai = createModelChannel({ id: "openai", enabled: true, interfaceType: "openai-image", models: ["native"], modelProfiles: [{ model: "native", capability: "image", protocol: "openai-image", capabilityConfig: { version: 1, image } }] });
const dreama = createModelChannel({ id: "dreama", enabled: true, interfaceType: "dreama-images-async", models: ["bridge"], modelProfiles: [{ model: "bridge", capability: "image", protocol: "dreama-images-async", capabilityConfig: { version: 1, image } }] });
const nativeModel = encodeChannelModel(openai.id, "native");
const bridgeModel = encodeChannelModel(dreama.id, "bridge");
const config = { ...defaultConfig, channels: [openai, dreama], model: nativeModel, imageModel: nativeModel };
const node: CanvasNodeData = { id: "source", type: CanvasNodeType.Image, title: "图片", width: 100, height: 100, position: { x: 0, y: 0 }, metadata: { model: nativeModel, content: "image.png" } };
let dispatched = 0;
const props = { node, imageDimensions: { width: 100, height: 100 }, scale: 1, config, onCancel: () => {}, onConfirm: () => { dispatched++; } };
const renderMask = () => { cursor = 0; return elements(CanvasImageMaskEditor(props)); };
let tree = renderMask();
expect(tree.find((element) => element.props["aria-label"] === "AI 修改")!.props.disabled).toBeFalsy();
tree.find((element) => element.props["aria-label"] === "局部重绘设置")!.props.onClick();
tree = renderMask();
const picker = tree.find((element) => element.props.capability === "image" && typeof element.props.onChange === "function")!;
picker.props.onChange(bridgeModel);
tree = renderMask();
const submit = tree.find((element) => element.props["aria-label"] === "AI 修改")!;
expect(submit.props.disabled).toBe(true);
expect(submit.props["aria-describedby"]).toBe("support-notice");
expect(tree.find((element) => element.props.role === "status")!.props.children).toContain("剧梦");
submit.props.onClick();
expect(dispatched).toBe(0);
tree.find((element) => element.props.capability === "image")!.props.onChange(nativeModel);
expect(renderMask().find((element) => element.props["aria-label"] === "AI 修改")!.props.disabled).toBeFalsy();
console.log("PASS: model changes disable an existing mask editor before submit");

state = []; cursor = 0;
const reason = "先绑定画布后再生成";
const menu = NodeDockMenuButton({ menuId: "portrait", label: "人像调整", openMenuId: "portrait", onOpenChange: () => {}, tools: [{ id: "portraitTexture", label: "质感调整 · 暂不可用", group: "portrait", order: 1, icon: null, disabled: true, disabledReason: reason, onClick: () => { dispatched++; } }] });
const item = menu.props.menu.items[0];
expect(item.disabled).toBe(true);
expect(elements(item.label).some((element) => element.props.children === reason)).toBe(true);
item.onClick({});
expect(dispatched).toBe(0);
console.log("PASS: unavailable menu items retain their explanation and ignore invocation");

const actualConfig = await import("../../src/stores/use-config-store");
const actualCanvasStore = await import("../../src/stores/canvas/use-canvas-store");
const source = { ...node, metadata: { ...node.metadata, model: bridgeModel } };
const draft = { ...node, id: "draft", metadata: { model: bridgeModel, composerContent: "检查表情", imageTool: { version: 1, kind: "angle", sourceNodeId: source.id }, emotionEdit: { sourceNodeId: source.id } } } as CanvasNodeData;
const canvas = { id: "canvas", nodes: [source, draft], integrations: { dreama: { schemaVersion: 1, projectId: 41, projectName: "项目", promptMode: "drama", aspectRatio: "1:1", defaultSeriesId: 12, revision: 1 } } };
const promptConfig = { ...config, model: bridgeModel, imageModel: bridgeModel };
mock.module("@/stores/use-config-store", () => ({ ...actualConfig, useEffectiveConfig: () => promptConfig }));
mock.module("@/stores/canvas/use-canvas-store", () => ({ ...actualCanvasStore, useCanvasStore: Object.assign((select: (value: unknown) => unknown) => select({ projects: [canvas] }), actualCanvasStore.useCanvasStore) }));
mock.module("@/components/canvas/canvas-reference-feedback", () => ({ CanvasReferenceFeedback: () => null, useCanvasReferenceCheck: () => ({ issues: [], selectedReferences: [], pending: false, checkFailed: false, retry: () => {} }) }));
mock.module("@/components/canvas/canvas-prompt-optimizer-drawer", () => ({ CanvasPromptOptimizerDrawer: () => null }));
for (const [path, name] of [["@/stores/use-creation-preferences-store", "useCreationPreferencesStore"], ["@/stores/use-plugin-store", "usePluginStore"], ["@/stores/use-user-store", "useUserStore"]]) {
    const module = await import(path);
    const store = module[name];
    mock.module(path, () => ({ ...module, [name]: Object.assign((select: (value: unknown) => unknown) => select(store.getState()), store) }));
}
const { CanvasNodePromptPanel } = await import("../../src/components/canvas/canvas-node-prompt-panel");
state = []; cursor = 0;
const panel = elements(CanvasNodePromptPanel({ projectId: canvas.id, node: draft, referenceNodes: canvas.nodes, isRunning: false, onPromptChange: () => {}, onConfigChange: () => {}, onGenerate: () => { dispatched++; } }));
const generate = panel.find((element) => element.props["aria-label"] === "生成")!;
expect(generate.props.disabled).toBe(true);
expect(panel.some((element) => element.props.role === "alert" && String(element.props.children).includes("精确表情编辑"))).toBe(true);
generate.props.onClick();
expect(dispatched).toBe(0);
console.log("PASS: stale emotion metadata disables a tool draft before generation");

dreama.apiKey = "fixture-only";
dreama.baseUrl = "http://127.0.0.1:18765";
dreama.modelProfiles![0]!.imageCapabilitiesVersion = "fixture-v1";
const sourceEdge = { id: "original", fromNodeId: source.id, toNodeId: draft.id };
delete draft.metadata!.emotionEdit;
const renderPrompt = (text: string, edges = [sourceEdge], metadata: any = {}) => {
    state = []; cursor = 0;
    draft.metadata = { model: bridgeModel, composerContent: text, imageTool: { version: 1, kind: "angle", sourceNodeId: source.id }, ...metadata };
    return elements(CanvasNodePromptPanel({ projectId: canvas.id, node: draft, referenceNodes: canvas.nodes, referenceConnections: edges,
        mentionReferences: [{ id: "skill", kind: "skill", label: "技能1", title: "技能", active: true, skill: { skillId: "test-skill", skillName: "技能", isAdded: true } } as any],
        isRunning: false, onPromptChange: () => {}, onConfigChange: (_id, patch) => { draft.metadata = { ...draft.metadata, ...patch }; }, onGenerate: () => { dispatched++; } }));
};
for (const [text, edges] of [["只有正文", [sourceEdge]], ["@[node:source] 正文", []]] as const) {
    const rendered = renderPrompt(text, [...edges]);
    const button = rendered.find(element => element.props["aria-label"] === "生成")!;
    expect(button.props.disabled).toBe(true);
    expect(rendered.some(element => element.props.role === "alert" && String(element.props.children).includes("原图"))).toBe(true);
    button.props.onClick();
    expect(dispatched).toBe(0);
}
const camera = renderPrompt("@[node:source] 原文", [sourceEdge], { cameraControl: { enabled: true } });
expect(camera.find(element => element.props["aria-label"] === "生成")!.props.disabled).toBe(true);
expect(camera.some(element => typeof element.props.onCameraControlChange === "function")).toBe(false);
const clearCamera = camera.find(element => element.props["aria-label"] === "关闭旧摄影设置")!;
expect(clearCamera).toBeDefined();
clearCamera.props.onClick();
expect(draft.metadata!.cameraControl?.enabled).toBe(false);
expect(draft.metadata!.composerContent).toBe("@[node:source] 原文");
const skills = renderPrompt("@[node:source] @[skill:test-skill]");
expect(skills.find(element => element.props["aria-label"] === "生成")!.props.disabled).toBe(true);
expect(skills.filter(element => Array.isArray(element.props.references)).every(element => element.props.references.every((ref: any) => ref.kind !== "skill"))).toBe(true);
expect(skills.some(element => element.props.role === "alert" && String(element.props.children).includes("技能"))).toBe(true);
console.log("PASS: missing original references and stale hidden instructions disable the rendered composer without rewriting prose");

const video = defaultModelCapabilityConfig("dreama-videos-async").video!;
const videoChannel = createModelChannel({ id: "dreama-video", enabled: true, baseUrl: "http://127.0.0.1:18765", apiKey: "fixture-only", interfaceType: "dreama-videos-async", models: ["video"], modelProfiles: [{ model: "video", capability: "video", protocol: "dreama-videos-async", videoCapabilitiesVersion: "fixture-v1", capabilityConfig: { version: 1, video: { ...video, ratios: ["1:1"], defaultRatio: "1:1" } } }] });
promptConfig.channels = [...promptConfig.channels, videoChannel];
draft.type = CanvasNodeType.Video;
const videoPanel = renderPrompt("原始视频正文", [], { model: "dreama-video::video", imageTool: undefined, cameraControl: { enabled: true } });
expect(videoPanel.some(element => typeof element.props.onCameraControlChange === "function")).toBe(false);
expect(videoPanel.find(element => element.props["aria-label"] === "生成")!.props.disabled).toBe(true);
videoPanel.find(element => element.props["aria-label"] === "关闭旧摄影设置")!.props.onClick();
expect(draft.metadata?.composerContent).toBe("原始视频正文");
draft.type = CanvasNodeType.Image;
const otherPanel = renderPrompt("原始图片正文", [], { model: nativeModel, imageTool: undefined });
expect(otherPanel.some(element => typeof element.props.onCameraControlChange === "function")).toBe(true);
expect(otherPanel.filter(element => Array.isArray(element.props.references)).some(element => element.props.references.some((ref: any) => ref.kind === "skill"))).toBe(true);
draft.type = CanvasNodeType.Text;
const textPanel = renderPrompt("文字正文", [], { imageTool: undefined });
expect(textPanel.filter(element => Array.isArray(element.props.references)).some(element => element.props.references.some((ref: any) => ref.kind === "skill"))).toBe(true);
draft.type = CanvasNodeType.Image;
console.log("PASS: Dreama video hides motion controls while other image and text authoring remain available");

const antd = await import("antd");
mock.module("antd", () => ({ ...antd, App: { ...antd.App, useApp: () => ({ message: { success: () => {}, warning: () => {} } }) } }));
const { CanvasNodeToolbar } = await import("../../src/components/canvas/canvas-node-toolbar");
const localText = createModelChannel({ id: "codex-local", enabled: true, interfaceType: "chat-completion", models: ["arbitrary-text"], modelProfiles: [{ model: "arbitrary-text", capability: "text", protocol: "chat-completion" }] });
const externalText = createModelChannel({ ...localText, id: "other-text" });
promptConfig.channels = [...promptConfig.channels, localText, externalText];
const toolbarProps = { projectId: canvas.id, node: { ...source, type: CanvasNodeType.Video }, viewport: { x: 0, y: 0, k: 1 }, containerRef: { current: null }, onReversePrompt: () => {} };
const renderToolbar = (compact: boolean) => {
    state = [null, compact ? 400 : 1000, { left: 100, top: 100 }]; cursor = 0;
    return elements(CanvasNodeToolbar(toolbarProps as any));
};
function videoToolIds(compact: boolean) {
    const menu = renderToolbar(compact).find((element) => element.props.menuId === "video-processing");
    return (menu?.props.tools || []).map((tool: any) => tool.id);
}
promptConfig.textModel = "codex-local::arbitrary-text";
for (const compact of [false, true]) expect(videoToolIds(compact)).not.toContain("video-reverse-prompt");
promptConfig.textModel = "other-text::arbitrary-text";
for (const compact of [false, true]) {
    expect(videoToolIds(compact)).toContain("video-reverse-prompt");
}
console.log("PASS: video reverse entry disappears for the Codex bridge in wide and compact toolbars");
