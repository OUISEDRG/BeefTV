import { expect, test } from "bun:test";
import { modelReasoningQueryKey, modelReasoningTarget, prepareReasoningUpdate, reasoningSelectOptions } from "@/lib/model-reasoning";
import { createModelChannel, defaultConfig } from "@/stores/use-config-store";
import type { ModelReasoningSnapshot } from "@/services/api/model-reasoning";

const channel = createModelChannel({ id: "my-local-provider", name: "我的本机服务", enabled: true, apiFormat: "openai", baseUrl: "http://127.0.0.1:18766/v1", apiKey: "not-sent-to-reasoning-api", models: ["text-a", "text-b"], modelProfiles: [{ model: "text-a", capability: "text", protocol: "chat-completion" }, { model: "text-b", capability: "text", protocol: "chat-completion" }] });
const config = { ...defaultConfig, channels: [channel], textModels: ["my-local-provider::text-a", "my-local-provider::text-b"] };
const snapshot: ModelReasoningSnapshot = { channelId: channel.id, revision: 7, overrides: { "text-a": "high" }, models: [
    { id: "text-a", displayName: "Text A", defaultReasoningEffort: "low", supportedReasoningEfforts: [{ reasoningEffort: "low", description: "low" }, { reasoningEffort: "high", description: "high" }, { reasoningEffort: "ultra", description: "ultra" }] },
    { id: "text-b", displayName: "Text B", defaultReasoningEffort: "medium", supportedReasoningEfforts: [{ reasoningEffort: "low", description: "low" }, { reasoningEffort: "medium", description: "medium" }] },
] };

test("resolves encoded and legacy bare model values without depending on a Codex channel id", () => {
    expect(modelReasoningTarget(config, "my-local-provider::text-a")).toEqual({ channelId: channel.id, model: "text-a", endpoint: "http://127.0.0.1:18766/v1" });
    expect(modelReasoningTarget(config, "text-a")).toEqual(modelReasoningTarget(config, "my-local-provider::text-a"));
    expect(modelReasoningTarget(config, "missing::text-a")).toBeNull();
    expect(modelReasoningTarget(config, "my-local-provider::missing")).toBeNull();
});

test("only enabled loopback text/chat-completion channels are eligible", () => {
    for (const baseUrl of ["http://localhost:1234/v1", "http://[::1]:1234/v1", "https://127.0.0.1/v1"]) {
        expect(modelReasoningTarget({ ...config, channels: [{ ...channel, baseUrl }] }, "text-a")).not.toBeNull();
    }
    for (const baseUrl of ["https://example.com/v1", "http://127.0.0.2/v1", "http://localhost.example.com/v1", "http://user:pass@localhost/v1", "http://localhost/v1?token=secret", "http://localhost/v1#fragment", "file:///tmp/settings"]) {
        expect(modelReasoningTarget({ ...config, channels: [{ ...channel, baseUrl }] }, "text-a")).toBeNull();
    }
    for (const update of [{ enabled: false }, { modelProfiles: [{ model: "text-a", capability: "image" as const, protocol: "chat-completion" }] }, { modelProfiles: [{ model: "text-a", capability: "text" as const, protocol: "responses" }] }]) {
        expect(modelReasoningTarget({ ...config, channels: [{ ...channel, ...update }] }, "text-a")).toBeNull();
    }
});

test("a profile-free channel requires the selected model in the text model list", () => {
    const bare = { ...config, channels: [{ ...channel, modelProfiles: [] }] };
    expect(modelReasoningTarget(bare, "text-a")).not.toBeNull();
    expect(modelReasoningTarget({ ...bare, textModels: [] }, "text-a")).toBeNull();
});

test("each model uses only native supported efforts and its actual default", () => {
    expect(reasoningSelectOptions(snapshot.models[0]).map(option => option.value)).toEqual(["", "low", "high", "ultra"]);
    expect(reasoningSelectOptions(snapshot.models[1]).map(option => option.value)).toEqual(["", "low", "medium"]);
    expect(reasoningSelectOptions(snapshot.models[1])[0].label).toBe("默认 · 中");
    const described = { ...snapshot.models[0], supportedReasoningEfforts: [{ reasoningEffort: "ultra", description: "Maximum reasoning with automatic task delegation" }] };
    expect(reasoningSelectOptions(described)[1].title).toBe("极致（ultra）");
});

test("save captures one model and revision before selection changes, with no credentials", () => {
    const target = modelReasoningTarget(config, "text-a")!;
    const update = prepareReasoningUpdate(target, snapshot, "ultra");
    target.model = "text-b";
    expect(update).toEqual({ channelId: channel.id, model: "text-a", effort: "ultra", expectedRevision: 7 });
    expect(prepareReasoningUpdate(target, snapshot, "")).toEqual({ channelId: channel.id, model: "text-b", effort: null, expectedRevision: 7 });
    expect(JSON.stringify(update)).not.toContain(channel.apiKey);
    expect(JSON.stringify(update)).not.toContain(channel.baseUrl);
});

test("wrong channel, stale missing model and unsupported effort cannot be submitted", () => {
    const target = modelReasoningTarget(config, "text-b")!;
    expect(() => prepareReasoningUpdate(target, snapshot, "ultra")).toThrow();
    expect(() => prepareReasoningUpdate({ ...target, channelId: "other" }, snapshot, "low")).toThrow();
    expect(() => prepareReasoningUpdate({ ...target, model: "retired" }, snapshot, "low")).toThrow();
    expect(() => prepareReasoningUpdate(target, { ...snapshot, revision: -1 }, "low")).toThrow();
});

test("models in one channel share settings cache, but channel address changes do not", () => {
    const first = modelReasoningTarget(config, "text-a")!;
    const second = modelReasoningTarget(config, "text-b")!;
    expect(modelReasoningQueryKey(first)).toEqual(modelReasoningQueryKey(second));
    expect(modelReasoningQueryKey(first)).not.toEqual(modelReasoningQueryKey({ ...first, endpoint: "http://localhost:9999/v1" }));
});
