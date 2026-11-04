import { http } from "./request";

export type ModelReasoningOption = { reasoningEffort: string; description: string };
export type ModelReasoningModel = {
    id: string;
    model?: string;
    displayName: string;
    defaultReasoningEffort: string;
    supportedReasoningEfforts: ModelReasoningOption[];
};
export type ModelReasoningSnapshot = {
    channelId: string;
    models: ModelReasoningModel[];
    overrides: Record<string, string>;
    revision: number;
};
export type ModelReasoningUpdate = {
    channelId: string;
    model: string;
    effort: string | null;
    expectedRevision: number;
};

export function getModelReasoning(channelId: string, signal?: AbortSignal) {
    return http.get<ModelReasoningSnapshot>("/workspace/model-config/reasoning", { params: { channelId }, signal });
}

export function saveModelReasoning(update: ModelReasoningUpdate) {
    return http.put<ModelReasoningSnapshot>("/workspace/model-config/reasoning", update);
}
