import { decodeChannelModel, normalizeModelOptionValue, type AiConfig } from "@/stores/use-config-store";
import type { ModelReasoningModel, ModelReasoningSnapshot, ModelReasoningUpdate } from "@/services/api/model-reasoning";

export type ModelReasoningTarget = { channelId: string; model: string; endpoint: string };

/** Only offer local reasoning settings for a configured, selectable text channel. */
export function modelReasoningTarget(config: AiConfig, modelKey: string): ModelReasoningTarget | null {
    const normalized = normalizeModelOptionValue(modelKey, config.channels);
    const selected = decodeChannelModel(normalized);
    if (!selected) return null;
    const channel = config.channels.find(item => item.id === selected.channelId);
    if (!channel || channel.enabled === false || !channel.models.includes(selected.model)) return null;
    const profile = channel.modelProfiles?.find(item => item.model === selected.model);
    if (profile ? profile.capability !== "text" : !config.textModels.includes(normalized)) return null;
    const protocol = profile?.protocol || channel.interfaceType || (channel.apiFormat === "openai" ? "chat-completion" : "");
    if (protocol !== "chat-completion") return null;
    try {
        const url = new URL(channel.baseUrl);
        if (!["http:", "https:"].includes(url.protocol) || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname.toLowerCase()) || url.username || url.password || url.search || url.hash) return null;
        return { channelId: selected.channelId, model: selected.model, endpoint: url.href };
    } catch {
        return null;
    }
}

export function modelReasoningQueryKey(target: ModelReasoningTarget | null) {
    // Credential-free endpoint identity prevents reusing the old cache after a channel is edited.
    return ["model-reasoning", target?.channelId || "", target?.endpoint || ""] as const;
}

export function reasoningEffortLabel(effort: string) {
    const labels: Record<string, string> = { none: "关闭", minimal: "极低", low: "低", medium: "中", high: "高", xhigh: "更高", max: "最高", ultra: "极致" };
    return labels[effort] || effort;
}

export function reasoningSelectOptions(model: ModelReasoningModel) {
    return [
        { value: "", label: `默认 · ${reasoningEffortLabel(model.defaultReasoningEffort)}`, title: `跟随模型默认（${model.defaultReasoningEffort}）` },
        ...model.supportedReasoningEfforts.map(option => ({ value: option.reasoningEffort, label: reasoningEffortLabel(option.reasoningEffort), title: `${reasoningEffortLabel(option.reasoningEffort)}（${option.reasoningEffort}）` })),
    ];
}

export function prepareReasoningUpdate(target: ModelReasoningTarget, snapshot: ModelReasoningSnapshot, selectedEffort: string): ModelReasoningUpdate {
    if (snapshot.channelId !== target.channelId || !Number.isSafeInteger(snapshot.revision) || snapshot.revision < 0) throw new Error("模型设置已经变化，请重新读取后再选择。");
    const model = snapshot.models.find(item => item.id === target.model);
    if (!model || selectedEffort !== "" && !model.supportedReasoningEfforts.some(item => item.reasoningEffort === selectedEffort)) throw new Error("该模型不支持所选推理强度，请重新选择。");
    return { channelId: target.channelId, model: target.model, effort: selectedEffort === "" ? null : selectedEffort, expectedRevision: snapshot.revision };
}
