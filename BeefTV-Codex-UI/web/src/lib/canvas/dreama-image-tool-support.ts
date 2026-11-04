import { channelHasGenerationCredential, modelOptionName, resolveModelChannel, resolveModelRequestConfig, type AiConfig } from "@/stores/use-config-store";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";
import type { DreamaCanvasBinding } from "@/types/dreama-workbench";
import { buildGenerationConfig } from "./canvas-generation-config";
import { freezeDreamaBinding, isDreamaProtocol } from "./dreama-workbench";
import { declaredGenerationConfigError } from "@/lib/declared-generation-config";

const imageGenerationTools = new Set(["maskEdit", "emotion", "angle", "lighting", "portraitTexture"]);

export type DreamaImageToolCanvas = { id: string; integrations?: { dreama?: DreamaCanvasBinding }; nodes: CanvasNodeData[] };
export type DreamaImageToolSupport = { disabled: boolean; hidden?: boolean; reason?: string };

export function dreamaImageToolSupport(toolId: string, node: CanvasNodeData, config: AiConfig, canvas?: DreamaImageToolCanvas, context: "source" | "generation" = "source"): DreamaImageToolSupport | undefined {
    if (!imageGenerationTools.has(toolId)) return undefined;
    const generation = buildGenerationConfig(config, node, "image");
    const invalid = declaredGenerationConfigError(generation, "image");
    const protocol = resolveModelRequestConfig(generation, generation.model).interfaceType;
    if (!isDreamaProtocol(protocol)) return invalid ? { disabled: true, reason: invalid } : undefined;
    // This bridge has no masked-edit transport; a stale catalog must not expose it.
    if (toolId === "maskEdit") return { disabled: true, hidden: true, reason: "剧梦暂不支持蒙版局部重绘" };
    if (toolId === "emotion") return { disabled: true, hidden: true, reason: "剧梦暂不支持精确表情编辑，需要蒙版与局部合成能力" };
    const blocked = (reason: string): DreamaImageToolSupport => ({ disabled: true, reason });
    if (invalid) return blocked(invalid);
    if (!canvas) return blocked("画布尚未加载，请返回画布确认项目与剧集绑定");
    const imageTool = context === "generation" ? node.metadata?.imageTool : undefined;
    const source = imageTool ? canvas?.nodes.find(item => item.id === imageTool.sourceNodeId) : node;
    if (!source || source.type !== CanvasNodeType.Image || !source.metadata?.content && !source.metadata?.storageKey) return blocked(imageTool ? "图片工具的原图已不存在，请重新选择原图" : "请先选择一张已有图片");
    try { freezeDreamaBinding(canvas.integrations?.dreama, canvas.id, node.id, canvas.nodes); }
    catch (error) { return blocked(error instanceof Error ? error.message : "请先完成画布项目与剧集绑定"); }
    const channel = resolveModelChannel(generation, generation.model);
    if (!channel.baseUrl.trim()) return blocked("请先配置剧梦渠道地址");
    if (!channelHasGenerationCredential(channel, generation.model)) return blocked("请先配置剧梦渠道凭据");
    const profile = channel.modelProfiles?.find(item => item.model === modelOptionName(generation.model));
    if (protocol !== "dreama-images-async" || profile?.capability !== "image" || !profile.capabilityConfig?.image || !profile.imageCapabilitiesVersion?.trim()) return blocked("当前型号缺少剧梦图片能力，请刷新目录或选择图片型号");
    if (!(profile.capabilityConfig.image.references.maxImages >= 1)) return blocked("当前型号不支持参考图编辑，请选择支持参考图片的型号");
    return { disabled: false };
}
