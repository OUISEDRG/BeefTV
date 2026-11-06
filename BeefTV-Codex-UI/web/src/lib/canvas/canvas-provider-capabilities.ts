import { modelCapabilityConfigFor } from "@/lib/model-capabilities";
import { hasDeclaredModelCapability } from "@/lib/model-selection";
import { channelHasGenerationCredential, isBuiltinBeefAPIChannel, normalizeModelOptionValue, resolveModelChannel, resolveModelRequestConfig, selectableModelsByCapability, type AiConfig } from "@/stores/use-config-store";
import { buildGenerationConfig, resolveCanvasGenerationModel } from "./canvas-generation-config";
import { isDreamaProtocol } from "./dreama-workbench";
import type { CanvasNodeData } from "@/types/canvas";
import type { WorkflowDefinition } from "@/services/api/canvas-workflows";

export function canvasAIUpscaleAvailable(config: AiConfig): boolean {
    return config.channels.some(channel => channel.enabled !== false && isBuiltinBeefAPIChannel(channel) && channelHasGenerationCredential(channel));
}

/** The installed codex-local chat adapter accepts text and images, not video attachments. */
export function canvasVideoReversePromptAvailable(config: AiConfig): boolean {
    const selected = buildGenerationConfig(config, undefined, "text");
    const channel = resolveModelChannel(selected, selected.model);
    return channel.id !== "codex-local" || resolveModelRequestConfig(selected, selected.model).interfaceType !== "chat-completion";
}

export type CanvasVideoProcessingOperation = "extend" | "inpaint" | "upscale";
export type CanvasVideoOperation = CanvasVideoProcessingOperation | "draft_to_final";
export type CanvasVideoOperationSupport = { available: boolean; model?: string; reason?: string };

/** Processing adapters use the ordinary canvas executor and its frozen project binding. */
export function canvasVideoOperationSupport(config: AiConfig, node: CanvasNodeData, operation: CanvasVideoOperation): CanvasVideoOperationSupport {
    const selected = buildGenerationConfig(config, node, "video");
    let model = selected.model;
    const protocol = resolveModelRequestConfig(selected, model).interfaceType;
    if (operation === "upscale") {
        const candidates = selectableModelsByCapability(config, "video");
        model = [model, ...candidates].find(value => candidates.includes(value)
            && resolveModelRequestConfig(config, value).interfaceType === "dreama-enhancement-async"
            && modelCapabilityConfigFor(config, value).video?.operations.includes("upscale")
            && resolveModelChannel(config, value).enabled !== false
            && channelHasGenerationCredential(resolveModelChannel(config, value), value)) || "";
        if (!model) return { available: false, reason: "尚未配置可用的剧梦视频增强渠道" };
    } else if (isDreamaProtocol(protocol)) {
        if (operation !== "extend") return { available: false, reason: "当前剧梦渠道尚未接入此视频处理功能" };
        const candidates = selectableModelsByCapability(config, "video");
        model = [model, ...candidates].find(value => candidates.includes(value) && resolveModelRequestConfig(config, value).interfaceType === "dreama-extension-async" && resolveModelChannel(config, value).enabled !== false && channelHasGenerationCredential(resolveModelChannel(config, value), value)) || "";
        if (!model) return { available: false, reason: "尚未配置可用的剧梦视频延长渠道" };
    } else if (hasDeclaredModelCapability(selected, model, "video") && !modelCapabilityConfigFor(selected, model).video?.operations.includes(operation)) {
        return { available: false, reason: "当前视频模型不支持此处理功能" };
    }
    return { available: Boolean(model), model, ...(!model ? { reason: "请先选择可用的视频模型" } : {}) };
}

/** These standalone generators do not yet carry the canvas binding/preview contract. */
export function canvasStandaloneImageGenerationAvailable(config: AiConfig): boolean {
    const model = resolveCanvasGenerationModel(config, config.imageModel, "image") || resolveCanvasGenerationModel(config, config.model, "image");
    return !model || !isDreamaProtocol(resolveModelRequestConfig(config, model).interfaceType);
}

export function canvasWorkflowModelAvailable(config: AiConfig, model: string): boolean {
    const configured = normalizeModelOptionValue(model, config.channels);
    return !configured || !isDreamaProtocol(resolveModelRequestConfig(config, configured).interfaceType);
}

export function canvasWorkflowExecutionUnavailableReason(config: AiConfig, definition: WorkflowDefinition): string {
    if (definition.nodes.some(node => node.operationId?.endsWith(".ai-upscale")) && !canvasAIUpscaleAvailable(config)) return "当前渠道不支持此工作流的 AI 超分步骤";
    if (definition.nodes.some(node => typeof node.config?.modelOption === "string" && !canvasWorkflowModelAvailable(config, node.config.modelOption))) return "剧梦生成暂不能通过可复用工作流执行，请返回画布确认项目和剧集后生成";
    return "";
}
