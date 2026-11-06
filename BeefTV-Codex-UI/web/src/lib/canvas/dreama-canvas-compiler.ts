import { buildNodeGenerationContext, compileNodeGenerationReferences } from "@/components/canvas/canvas-node-generation";
import { buildConfirmedGenerationConfig } from "@/pages/canvas/canvas-assistant-proposal-execution";
import { resolvedCanvasGenerationRequirements } from "@/lib/canvas/canvas-generation-config";
import { buildVideoGenerationMetadata } from "@/lib/canvas/canvas-project-generation";
import { assertDreamaReferences, buildDreamaGenerationPreview, dreamaCanvasVideoConfig, freezeDreamaBinding, isDreamaProtocol, mergeDreamaProviderOptions } from "@/lib/canvas/dreama-workbench";
import { prepareDreamaVoiceProviderOptions } from "@/lib/canvas/dreama-voice-generation";
import { declaredGenerationConfigError } from "@/lib/declared-generation-config";
import { modelCapabilityConfigFor } from "@/lib/model-capabilities";
import { modelGroupReferenceLimits, modelRequestOptions } from "@/lib/model-selection";
import { modelOptionName, normalizeModelOptionValue, resolveModelChannel, resolveModelRequestConfig, type AiConfig } from "@/stores/use-config-store";
import { CanvasNodeType, type CanvasNodeData, type CanvasConnection } from "@/types/canvas";
import type { DreamaCanvasBinding } from "@/types/dreama-workbench";

type Input = {
    schemaVersion: 1;
    canvas: { id: string; revision: number; nodes: CanvasNodeData[]; connections: CanvasConnection[]; integrations?: { dreama?: DreamaCanvasBinding } };
    config: AiConfig; nodeIds: string[]; assets: never[];
};

/** Pure headless adapter over the UI compiler. No API, upload, task or payment calls. */
export async function compileDreamaCanvas(input: Input) {
    if (input.schemaVersion !== 1 || !input.canvas?.id || !Array.isArray(input.nodeIds) || !input.nodeIds.length || input.nodeIds.length > 8 || new Set(input.nodeIds).size !== input.nodeIds.length) throw new Error("请选择当前画布的 1 至 8 个不同视频节点");
    // Never let the UI defaults mutate the caller's authoritative document.
    const { canvas, config: base } = structuredClone(input);
    const items = [];
    for (const nodeId of input.nodeIds) {
        const node = canvas.nodes.find(item => item.id === nodeId);
        if (!node || node.type !== CanvasNodeType.Video) throw new Error("文件夹工作流目前仅准备普通视频节点，请在画布内操作其他类型");
        const meta = node.metadata || {};
        const modelKey = meta.model || "";
        if (!modelKey.includes("::") || !normalizeModelOptionValue(modelKey, base.channels)) throw new Error("节点已保存型号不可用，请明确选择当前目录中的型号");
        let selected = { ...base, model: modelKey, taskWorkflowProvider: "model" as const,
            size: meta.size ?? base.size, quality: meta.quality ?? base.quality, videoSeconds: meta.seconds ?? base.videoSeconds,
            vquality: meta.vquality ?? base.vquality, videoGenerateAudio: meta.generateAudio ?? base.videoGenerateAudio, videoWatermark: meta.watermark ?? base.videoWatermark };
        const channel = resolveModelChannel(selected, modelKey);
        const request = resolveModelRequestConfig(selected, modelKey);
        if (channel.enabled === false || !isDreamaProtocol(request.interfaceType) || !request.baseUrl || !request.apiKey) throw new Error("节点需要已启用且配置完整的剧梦型号");
        if (!channel.modelProfiles?.find(item => item.model === modelOptionName(modelKey))?.videoCapabilitiesVersion) throw new Error("型号尚未取得已验证能力目录，请刷新模型目录");
        const binding = freezeDreamaBinding(canvas.integrations?.dreama, canvas.id, nodeId, canvas.nodes);
        const ratio = dreamaCanvasVideoConfig(selected, canvas.integrations?.dreama, meta.size);
        if (ratio.error) throw new Error(ratio.error);
        selected = { ...selected, ...ratio.config, taskWorkflowProvider: "model" };
        const declaredError = declaredGenerationConfigError(selected, "video");
        if (declaredError) throw new Error(declaredError);
        if (meta.cameraControl?.enabled || meta.videoCameraMovePrompt || meta.videoDraftTaskId || meta.workflowKind || meta.videoEditOperation === "concat") throw new Error("此节点包含文件夹工作流尚未接通的设置，请在画布内检查后操作");
        const prompt = meta.composerContent ?? meta.prompt ?? "";
        if (typeof prompt !== "string" || /@\[skill:/u.test(prompt)) throw new Error("请将技能内容明确写入提示词，再进行生成前检查");
        let context = buildNodeGenerationContext(nodeId, canvas.nodes, canvas.connections || [], prompt, [], true, { preserveMediaBindings: true, preservePromptText: true, referenceOnly: true });
        if (context.characterReferences.length || /@\[asset:/u.test(prompt)) throw new Error("请将角色卡或资产库引用展开为已保存的图片节点后再准备");
        const requirements = resolvedCanvasGenerationRequirements(selected, node, "video", { textCount: context.textCount, imageCount: context.imageCount, videoCount: context.videoCount, audioCount: context.audioCount, characterCount: 0 });
        const config = buildConfirmedGenerationConfig(selected, node, "video", requirements, modelKey);
        const metadata = buildVideoGenerationMetadata(node, context, config);
        const capability = modelCapabilityConfigFor(config, modelKey);
        if (!capability.video?.operations.includes(metadata.videoEditOperation!)) throw new Error("当前型号不支持节点指定的操作");
        context = compileNodeGenerationReferences(context, config, metadata.videoEditOperation, { referenceOnly: true });
        assertDreamaReferences(context, modelGroupReferenceLimits(config, modelKey, "video", requirements), metadata.videoEditOperation);
        const maximumPrompt = capability.video?.references.promptMaxChars;
        if (maximumPrompt && [...context.prompt].length > maximumPrompt) throw new Error(`提示词超过当前型号上限 ${maximumPrompt} 字符，请拆分镜头`);
        const references = (values: Array<{ id: string; storageKey?: string; mimeType?: string; type?: string; width?: number; height?: number; durationMs?: number }>) => values.map(value => {
            if (!value.storageKey?.startsWith("resource:")) throw new Error("参考素材必须先保存到当前工作区，不能使用临时图片或外部链接");
            return { id: value.id, storageKey: value.storageKey, mimeType: value.mimeType ?? value.type, width: value.width, height: value.height, durationMs: value.durationMs };
        });
        const referenceImages = references(context.referenceImages), referenceVideos = references(context.referenceVideos), referenceAudios = references(context.referenceAudios);
        // The host validates owned/ready audio metadata after compilation; no media bytes enter this snapshot.
        context.dreamaVoiceProviderOptions = mergeDreamaProviderOptions(binding, await prepareDreamaVoiceProviderOptions(context, config, "video", async resourceId => resourceId));
        const finalError = declaredGenerationConfigError(config, "video");
        if (finalError) throw new Error(finalError);
        if (!context.prompt.trim() && request.interfaceType !== "dreama-enhancement-async") throw new Error("请输入生成内容");
        const parameters = modelRequestOptions(config, "video");
        items.push({ nodeId, kind: "video", modelKey, prompt: context.prompt,
            config: { interfaceType: request.interfaceType, model: modelOptionName(modelKey), ...parameters, capabilityConfig: capability },
            metadata: { ...metadata, providerOptions: context.dreamaVoiceProviderOptions }, referenceImages, referenceVideos, referenceAudios,
            preview: buildDreamaGenerationPreview(binding, modelKey, "video", parameters, context) });
    }
    return { schemaVersion: 1, items };
}
