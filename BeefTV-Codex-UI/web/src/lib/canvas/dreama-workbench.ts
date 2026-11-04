import type { CanvasNodeData } from "@/types/canvas";
import type { DreamaCanvasBinding, DreamaGenerationBinding, DreamaGenerationPreview, DreamaProject } from "@/types/dreama-workbench";
import type { ModelReferenceLimits } from "@/lib/model-selection";
import { modelOptionName, resolveModelChannel, resolveModelRequestConfig, type AiConfig, type ModelChannel } from "@/stores/use-config-store";
import { isFrameNode } from "@/lib/canvas/canvas-frame";
import type { DreamaTask } from "@/services/api/dreama-workbench";
import { modelCapabilityConfigFor } from "@/lib/model-capabilities";

const positiveId = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) > 0;
const dreamaProtocols = new Set(["dreama-images-async", "dreama-videos-async", "dreama-extension-async", "dreama-enhancement-async", "dreama-audio-async"]);
export const isDreamaProtocol = (protocol: string | undefined) => Boolean(protocol && dreamaProtocols.has(protocol));

export function dreamaCanvasVideoConfig(config: AiConfig, binding: DreamaCanvasBinding | undefined, storedRatio?: string): { config: AiConfig; error: string; ratio?: string; ratioConflict: boolean } {
    if (!binding) return { config, error: "", ratioConflict: false };
    const protocol = resolveModelRequestConfig(config, config.model).interfaceType;
    if (!isDreamaProtocol(protocol)) return { config, error: "", ratioConflict: false };
    const channel = resolveModelChannel(config, config.model);
    const model = modelOptionName(config.model);
    const profile = channel.modelProfiles?.find(item => item.model === model);
    if (profile?.capabilityConfig?.video?.ratioSource !== "project" && protocol !== "dreama-videos-async") return { config, error: "", ratioConflict: false };
    const allowedRatios = profile?.capabilityConfig?.video?.ratios || [];
    const ratio = binding.aspectRatio;
    const ratioConflict = Boolean(storedRatio && storedRatio !== ratio);
    const supported = !allowedRatios.length || allowedRatios.includes(ratio);
    const error = !supported ? `当前型号不支持项目比例 ${ratio}，请更换型号` : ratioConflict ? `节点已保存比例 ${storedRatio} 与项目比例 ${ratio} 不一致，请明确选择使用项目比例` : "";
    const video = modelCapabilityConfigFor(config, config.model).video;
    const effective = { ...config, size: storedRatio || ratio, channels: config.channels.map(item => item.id !== channel.id || !supported || !video ? item : {
        ...item, modelProfiles: item.modelProfiles?.map(value => value.model !== model ? value : { ...value, capabilityConfig: { ...value.capabilityConfig, version: 1 as const, video: { ...video, ratios: [ratio], defaultRatio: ratio } } }),
    }) };
    return { config: effective, error, ratio, ratioConflict };
}

export function dreamaTaskPresentation(task: Pick<DreamaTask, "status" | "stage" | "cancelable">) {
    const labels: Record<string, string> = {
        preparation_queued: "等待准备", preparing: "准备中", prepared: "等待执行", countdown: "等待执行", submitting: "提交中",
        queued: "排队中", submitted: "已提交", processing: "生成中", running: "生成中", completed: "已完成", succeeded: "已完成",
        failed: "失败", canceled: "已取消", cancelled: "已取消", unknown: "等待核对", submission_unknown: "提交结果待核对",
        download_failed: "结果待取回", binding_verification_required: "项目归属待核验",
    };
    const terminal = ["completed", "succeeded", "failed", "cancelled", "canceled"].includes(task.status);
    const recovery = ["unknown", "submission_unknown", "download_failed", "binding_verification_required"].includes(task.status);
    const stages: Record<string, string> = { upload: "上传参考素材", save: "保存生成结果", poll: "查询生成进度", submit: "提交生成请求", download: "取回生成结果" };
    const reference = task.stage?.match(/^reference:(\d+)$/);
    const detail = terminal || recovery || task.status === "prepared" ? undefined : reference ? `准备第 ${reference[1]} 项参考素材` : stages[task.stage || ""];
    return { label: labels[task.status] || "状态待刷新", detail, pending: !terminal && (task.cancelable || Boolean(labels[task.status])) };
}

export function dreamaWorkbenchChannel(config: AiConfig): ModelChannel | undefined {
    return config.channels.find(channel => {
        if (!channel.enabled || !(isDreamaProtocol(channel.interfaceType) || channel.modelProfiles?.some(profile => isDreamaProtocol(profile.protocol)))) return false;
        try {
            const url = new URL(channel.baseUrl);
            return ["http:", "https:"].includes(url.protocol) && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname) && !url.username && !url.password && !url.search && !url.hash;
        } catch { return false; }
    });
}

export function bindDreamaProject(current: DreamaCanvasBinding | undefined, project: DreamaProject, defaultSeriesId: number): DreamaCanvasBinding {
    if (current && current.projectId !== project.id) throw new Error("当前画布已绑定剧梦项目，请先复制画布再绑定其他项目");
    if (project.promptMode !== "drama") throw new Error("当前仅支持已接通的 drama 剧本项目");
    if (!positiveId(project.id) || !positiveId(defaultSeriesId) || !project.aspectRatio?.trim()) throw new Error("请选择有效的剧梦项目和默认剧集");
    return { schemaVersion: 1, projectId: project.id, projectName: project.name, promptMode: "drama", aspectRatio: project.aspectRatio, defaultSeriesId, revision: (current?.revision || 0) + 1 };
}

export function freezeDreamaBinding(binding: DreamaCanvasBinding | undefined, canvasId: string, nodeId: string, nodes: CanvasNodeData[]): DreamaGenerationBinding {
    if (!binding || binding.schemaVersion !== 1 || binding.promptMode !== "drama" || !positiveId(binding.projectId) || !positiveId(binding.revision) || !binding.aspectRatio?.trim()) throw new Error("请先在剧梦工作台绑定此画布的项目和默认剧集");
    if (!canvasId.trim() || !nodeId.trim()) throw new Error("画布或生成节点不存在");
    const byId = new Map(nodes.map(node => [node.id, node]));
    let current = byId.get(nodeId);
    if (!current) throw new Error("生成节点已不存在");
    const seen = new Set<string>();
    const series = new Set<number>();
    while (current) {
        if (seen.has(current.id)) throw new Error("剧集分组存在循环，请修复分组关系");
        seen.add(current.id);
        if (current.metadata?.dreamaSeriesId !== undefined) {
            if (!isFrameNode(current) || !positiveId(current.metadata.dreamaSeriesId)) throw new Error("剧集分组绑定无效，请重新选择剧集");
            series.add(current.metadata.dreamaSeriesId);
        }
        if (!current.parentId) break;
        current = byId.get(current.parentId);
        if (!current) throw new Error("节点的分组已不存在，无法确定剧集归属");
    }
    if (series.size > 1) throw new Error("节点属于多个不同剧集分组，请保留唯一剧集归属");
    const seriesId = [...series][0] ?? binding.defaultSeriesId;
    if (!positiveId(seriesId)) throw new Error("请设置默认剧集或将节点放入已绑定剧集的分组");
    return { schemaVersion: 1, canvasId, nodeId, projectId: binding.projectId, seriesId, promptMode: "drama", aspectRatio: binding.aspectRatio, bindingRevision: binding.revision };
}

export function withoutDreamaBinding<T extends { integrations?: { dreama?: DreamaCanvasBinding }; nodes: CanvasNodeData[] }>(source: T): T {
    const copy = structuredClone(source);
    if (copy.integrations) delete copy.integrations.dreama;
    copy.nodes.forEach(node => { if (node.metadata) { delete node.metadata.dreamaSeriesId; delete node.metadata.dreamaGenerationPreview; } });
    return copy;
}

export function mergeDreamaProviderOptions(binding: DreamaGenerationBinding, options?: Record<string, Record<string, unknown>>): Record<string, Record<string, unknown>> {
    return { ...structuredClone(options || {}), dreama: { ...binding } };
}

type ReferenceContext = { prompt: string; referenceImages: Array<{ id: string }>; referenceVideos: Array<{ id: string }>; referenceAudios: Array<{ id: string }> };
/** Consumes the existing compiler's final order; never independently reorders media. */
export function assertDreamaReferences(context: ReferenceContext, limits?: ModelReferenceLimits, operation?: string) {
    const arrays = { 图片: context.referenceImages, 视频: context.referenceVideos, 音频: context.referenceAudios };
    const maximum = { 图片: limits?.maxImages, 视频: limits?.maxVideos, 音频: limits?.maxAudios };
    const used = new Set<string>();
    if (/@\[(?:node|asset):[^\]]+\]/u.test(context.prompt)) throw new Error("存在未解析的素材引用，请重新选择引用");
    for (const match of context.prompt.matchAll(/@(图片|视频|音频)(\d+)/gu)) {
        const kind = match[1] as keyof typeof arrays;
        const index = Number(match[2]);
        if (index < 1 || index > arrays[kind].length) throw new Error(`${match[0]} 没有对应的素材，请重新选择引用`);
        used.add(`${kind}${index}`);
    }
    for (const kind of Object.keys(arrays) as Array<keyof typeof arrays>) {
        const max = maximum[kind];
        if (max !== undefined && arrays[kind].length > max) throw new Error(`${kind}参考数量超过当前模型上限 ${max}`);
        if (operation === "reference_to_video" || operation === "image") arrays[kind].forEach((_, index) => {
            if (!used.has(`${kind}${index + 1}`)) throw new Error(`${kind}${index + 1} 尚未在提示词中引用，请添加 @ 引用或移除连接`);
        });
    }
}

export function buildDreamaGenerationPreview(binding: DreamaGenerationBinding, model: string, mode: string, parameters: Record<string, unknown>, context: {
    prompt: string; referenceImages: Array<{ id: string; name: string }>; referenceVideos: Array<{ id: string; name: string }>; referenceAudios: Array<{ id: string; name: string }>;
    dreamaVoiceProviderOptions?: Record<string, Record<string, unknown>>;
}): DreamaGenerationPreview {
    const voices = Object.values(context.dreamaVoiceProviderOptions || {}).flatMap(options => Array.isArray(options.character_voices) ? options.character_voices : []);
    return {
        binding: { ...binding }, model, mode, prompt: context.prompt, parameters: structuredClone(parameters),
        media: [["图片", context.referenceImages], ["视频", context.referenceVideos], ["音频", context.referenceAudios]].flatMap(([kind, items]) =>
            (items as Array<{ id: string; name: string }>).map((item, index) => ({ kind: String(kind), index: index + 1, id: item.id, name: item.name }))),
        voices: voices.map(voice => ({ image_index: voice.image_index, name: voice.name, ...(voice.voice_id ? { voice_id: voice.voice_id } : {}), ...(voice.audio ? { sample: true } : {}) })),
    };
}
