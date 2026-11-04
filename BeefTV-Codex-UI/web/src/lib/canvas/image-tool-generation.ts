import { buildImageGenerationMetadata } from "@/lib/canvas/canvas-project-generation";
import { mediaGeneratorMetadata } from "@/lib/canvas/canvas-node-semantics";
import { canvasNodeMentionToken } from "@/lib/canvas/canvas-resource-references";
import { buildPortraitTexturePrompt, normalizePortraitTextureSettings, type PortraitTextureSettings } from "@/lib/canvas/canvas-portrait-texture";
import type { AiConfig } from "@/stores/use-config-store";
import { CanvasNodeType, type CanvasConnection, type CanvasNodeData, type CanvasNodeMetadata } from "@/types/canvas";

type ImageTool = NonNullable<CanvasNodeMetadata["imageTool"]>;
const portraitStart = "【人物质感调节】";
const portraitEnd = "【调节要求结束】";

export function buildImageToolPrompt(sourceNodeId: string, instruction: string): string {
    return `参考原图：${canvasNodeMentionToken(sourceNodeId)}\n${instruction}`;
}

function portraitInstructions(settings: unknown): string {
    return `${portraitStart}\n${buildPortraitTexturePrompt("", settings)}\n${portraitEnd}`;
}

export function createImageToolGenerationNode(input: {
    source: CanvasNodeData; id: string; title: string; kind: ImageTool["kind"]; instruction?: string; config: AiConfig; portraitTexture?: PortraitTextureSettings;
}): CanvasNodeData {
    const { source, id, title, kind, config } = input;
    const portraitTexture = kind === "portraitTexture" ? normalizePortraitTextureSettings(input.portraitTexture) : undefined;
    const prompt = buildImageToolPrompt(source.id, portraitTexture ? portraitInstructions(portraitTexture) : input.instruction || "");
    return {
        id, type: CanvasNodeType.Image, title, parentId: source.parentId,
        position: { x: source.position.x + source.width + 96, y: source.position.y }, width: source.width, height: source.height,
        metadata: mediaGeneratorMetadata({
            ...buildImageGenerationMetadata("edit", config, 1, []), status: "idle", generationMode: "image", prompt, composerContent: prompt,
            imageTool: { version: 1, kind, sourceNodeId: source.id, ...(portraitTexture ? { portraitTexture } : {}) },
        }),
    };
}

/** Slider changes are authored into the editor immediately; submission only reads that visible text. */
export function updateImageToolPortraitTexture(node: CanvasNodeData, prompt: string, value: unknown): Partial<CanvasNodeMetadata> {
    const tool = node.metadata?.imageTool;
    if (tool?.version !== 1 || tool.kind !== "portraitTexture") return { portraitTexture: normalizePortraitTextureSettings(value) };
    const settings = normalizePortraitTextureSettings(value);
    const start = prompt.indexOf(portraitStart);
    const end = start < 0 ? -1 : prompt.indexOf(portraitEnd, start);
    const next = start >= 0 && end >= 0
        ? prompt.slice(0, start) + portraitInstructions(settings) + prompt.slice(end + portraitEnd.length)
        : `${prompt}\n${portraitInstructions(settings)}`;
    return { imageTool: { ...tool, portraitTexture: settings }, prompt: next, composerContent: next, generationPreflightError: undefined };
}

export function dreamaImageToolPromptError(node: CanvasNodeData): string | undefined {
    if (node.metadata?.portraitTexture) return "旧人物质感配置尚未写入提示词，请从原图重新打开人物质感工具；原节点正文已保留。";
    return undefined;
}

export function imageToolReferenceError(node: CanvasNodeData, nodes: CanvasNodeData[], connections: CanvasConnection[], prompt: string): string | undefined {
    try { assertImageToolGeneration(node, nodes, connections, prompt); }
    catch (error) { return error instanceof Error ? error.message : "图片工具的原图引用已失效"; }
    return undefined;
}

/** Persisted tool metadata is descriptive, never an exemption from normal prompt/reference checks. */
export function assertImageToolGeneration(node: CanvasNodeData, nodes: CanvasNodeData[], connections: CanvasConnection[], prompt: string): void {
    const tool = node.metadata?.imageTool;
    if (!tool) return;
    if (tool.version !== 1 || !["angle", "lighting", "portraitTexture"].includes(tool.kind) || !tool.sourceNodeId?.trim()) throw new Error("图片工具配置已失效，请从原图重新打开工具");
    const source = nodes.find(item => item.id === tool.sourceNodeId);
    if (source?.type !== CanvasNodeType.Image || !source.metadata?.content && !source.metadata?.storageKey) throw new Error("图片工具的原图已不存在，请重新选择原图");
    if (!connections.some(edge => edge.fromNodeId === source.id && edge.toNodeId === node.id)) throw new Error("图片工具缺少原图连接，请重新连接原图");
    if (!prompt.includes(canvasNodeMentionToken(source.id))) throw new Error("图片工具提示词必须明确引用原图，请恢复原图的 @ 引用");
}
