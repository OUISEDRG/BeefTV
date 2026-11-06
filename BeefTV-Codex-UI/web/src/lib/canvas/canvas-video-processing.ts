import { nanoid } from "nanoid";
import { modelCapabilityConfigFor } from "@/lib/model-capabilities";
import { resolveModelGenerationDefaults, resolveModelVideoBooleanOptions } from "@/lib/model-selection";
import type { AiConfig } from "@/stores/use-config-store";
import { CanvasNodeType, type CanvasConnection, type CanvasNodeData } from "@/types/canvas";
import { canvasVideoOperationSupport, type CanvasVideoProcessingOperation } from "./canvas-provider-capabilities";

export const canvasVideoProcessingLabels: Record<CanvasVideoProcessingOperation, string> = {
    extend: "视频延长",
    upscale: "视频增强",
    inpaint: "视频局部修改",
};

/** A processing shortcut prepares a separate draft; submission stays in the composer. */
export function createCanvasVideoProcessingDraft(config: AiConfig, source: CanvasNodeData, operation: CanvasVideoProcessingOperation): { node: CanvasNodeData; connection: CanvasConnection } {
    if (source.type !== CanvasNodeType.Video || (!source.metadata?.content && !source.metadata?.storageKey)) throw new Error("请先选择可读取的视频");
    const support = canvasVideoOperationSupport(config, source, operation);
    if (!support.available || !support.model) throw new Error(support.reason || "当前视频处理功能不可用");
    const profile = modelCapabilityConfigFor(config, support.model).video;
    const defaults = resolveModelGenerationDefaults(config, support.model, "video", operation === "upscale" ? {} : {
        videoSeconds: source.metadata?.seconds,
        vquality: source.metadata?.vquality,
        videoGenerateAudio: source.metadata?.generateAudio,
        videoWatermark: source.metadata?.watermark,
    });
    const booleans = resolveModelVideoBooleanOptions(config, support.model, defaults);
    const prompt = operation === "extend"
        ? "基于 @视频1 自然顺延后续动作、镜头运动与环境氛围，保持人物与画风一致"
        : operation === "inpaint" ? "基于 @视频1 进行画面修改：保持原有构图与节奏，将主体调整为 " : "";
    const node: CanvasNodeData = {
        id: nanoid(),
        type: CanvasNodeType.Video,
        title: `${source.title || "视频"} · ${canvasVideoProcessingLabels[operation]}`.slice(0, 36),
        parentId: source.parentId,
        position: { x: source.position.x + source.width + 96, y: source.position.y },
        width: source.width,
        height: source.height,
        metadata: {
            status: "idle",
            generationMode: "video",
            prompt,
            composerContent: prompt,
            model: support.model,
            ...(operation === "upscale" ? {} : { size: "adaptive" }),
            ...(profile?.durationSupported === false ? {} : { seconds: defaults.videoSeconds }),
            ...(profile?.resolutions.length ? { vquality: defaults.vquality } : {}),
            generateAudio: booleans.videoGenerateAudio,
            watermark: booleans.videoWatermark,
            draft: "false",
            videoEditOperation: operation,
            generatedFromNodeId: source.id,
        },
    };
    return { node, connection: { id: nanoid(), fromNodeId: source.id, toNodeId: node.id } };
}
