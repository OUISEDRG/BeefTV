import { expect, test } from "bun:test";
import { apiClient } from "../src/services/api/request";
import { dreamaApi } from "../src/services/api/dreama-workbench";

test("workbench routes use saved channel identity and preserve cancellation semantics", async () => {
    const original = apiClient.defaults.adapter;
    const calls: Array<{ url?: string; method?: string; params: unknown; body: unknown; signal: unknown }> = [];
    const controller = new AbortController();
    apiClient.defaults.adapter = async config => {
        calls.push({ url: config.url, method: config.method, params: config.params, body: config.data ? JSON.parse(config.data) : undefined, signal: config.signal });
        return { data: { code: 0, data: { items: [] } }, status: 200, statusText: "OK", headers: {}, config };
    };
    try {
        await dreamaApi.projects("saved", controller.signal);
        await dreamaApi.series("saved", 42, controller.signal);
        await dreamaApi.createSeries("saved", 42, "第二集");
        await dreamaApi.tasks("saved", "canvas/id", controller.signal);
        await dreamaApi.cancelTask("saved", "job/id");
        await dreamaApi.refreshCatalog("saved", 7);
        expect(calls[0]).toEqual({ url: "/integrations/dreama/v1/projects", method: "get", params: { channelId: "saved" }, body: undefined, signal: controller.signal });
        expect(calls[1]?.params).toEqual({ channelId: "saved", projectId: 42 });
        expect(calls[2]?.body).toEqual({ channelId: "saved", projectId: 42, name: "第二集" });
        expect(calls[3]?.params).toEqual({ channelId: "saved", canvasId: "canvas/id" });
        expect(calls[4]?.url).toBe("/integrations/dreama/v1/tasks/job%2Fid/cancel");
        expect(calls[4]?.body).toEqual({ channelId: "saved" });
        expect(calls[5]?.body).toEqual({ channelId: "saved", expectedRevision: 7 });
    } finally { apiClient.defaults.adapter = original; }
});
