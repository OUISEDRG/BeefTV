import { expect, test } from "bun:test";
import { App } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { ModelReasoningPicker } from "@/components/model-reasoning-picker";
import { modelReasoningQueryKey, modelReasoningTarget } from "@/lib/model-reasoning";
import { createModelChannel, defaultConfig } from "@/stores/use-config-store";
import type { ModelReasoningSnapshot } from "@/services/api/model-reasoning";

const channel = createModelChannel({ id: "local-provider", name: "Local", enabled: true, baseUrl: "http://localhost:18766/v1", apiFormat: "openai", models: ["a", "b"], modelProfiles: ["a", "b"].map(model => ({ model, capability: "text", protocol: "chat-completion" })) });
const config = { ...defaultConfig, channels: [channel], textModels: ["local-provider::a", "local-provider::b"] };
const snapshot: ModelReasoningSnapshot = { channelId: channel.id, revision: 2, overrides: { a: "high" }, models: [
    { id: "a", displayName: "A", defaultReasoningEffort: "low", supportedReasoningEfforts: [{ reasoningEffort: "low", description: "Low" }, { reasoningEffort: "high", description: "High" }] },
    { id: "b", displayName: "B", defaultReasoningEffort: "medium", supportedReasoningEfforts: [{ reasoningEffort: "medium", description: "Medium" }] },
] };

test("same cached state renders saved strength and each model's native default without generation", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    client.setQueryData(modelReasoningQueryKey(modelReasoningTarget(config, "a")), snapshot);
    const html = renderToStaticMarkup(<App><QueryClientProvider client={client}>
        <ModelReasoningPicker config={config} modelKey="local-provider::a" />
        <ModelReasoningPicker config={config} modelKey="local-provider::b" disabled />
    </QueryClientProvider></App>);
    expect(html).toContain('aria-label="推理强度"');
    expect(html).toContain("推理 高");
    expect(html).toContain("推理 默认 · 中");
    expect(html).toContain("ant-select-disabled");
    client.clear();
});

test("non-local models do not gain a broken reasoning control", () => {
    const client = new QueryClient();
    const remote = { ...config, channels: [{ ...channel, baseUrl: "https://example.com/v1" }] };
    const html = renderToStaticMarkup(<App><QueryClientProvider client={client}><ModelReasoningPicker config={remote} modelKey="local-provider::a" /></QueryClientProvider></App>);
    expect(html).not.toContain('aria-label="推理强度"');
    client.clear();
});
