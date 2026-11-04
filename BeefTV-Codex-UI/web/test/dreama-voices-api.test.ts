import { expect, test } from "bun:test";
import { apiClient } from "@/services/api/request";
import { listDreamaVoices } from "@/services/api/dreama-voices";
import { createModelChannel, defaultConfig } from "@/stores/use-config-store";

test("voice browsing sends only the saved channel identity to the native backend, never credentials or generation", async () => {
    const original = apiClient.defaults.adapter;
    const requests: Array<{ method?: string; url?: string; params: unknown; body: unknown; authorization: unknown }> = [];
    apiClient.defaults.adapter = async config => {
        requests.push({ method: config.method, url: config.url, params: config.params, body: config.data, authorization: config.headers.get("Authorization") });
        return { data: { code: 0, data: { channelId: "local-voice", voices: [{ id: "123", name: "原声", source: "personal", previewUrl: "https://cdn.example/audio.mp3" }] } }, status: 200, statusText: "OK", headers: {}, config };
    };
    try {
        const channel = createModelChannel({ id: "local-voice", enabled: true, baseUrl: "http://127.0.0.1:18765/audio", interfaceType: "dreama-audio-async", apiKey: "private-never-send" });
        expect((await listDreamaVoices({ ...defaultConfig, channels: [channel] }))[0].name).toBe("原声");
        expect(requests).toEqual([{ method: "get", url: "/workspace/model-config/voices", params: { channelId: "local-voice" }, body: undefined, authorization: undefined }]);
    } finally { apiClient.defaults.adapter = original; }
});
