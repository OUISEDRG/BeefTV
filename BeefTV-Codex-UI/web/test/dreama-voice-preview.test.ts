import { expect, test } from "bun:test";
import { apiClient } from "@/services/api/request";
import { resolveDreamaVoicePreview } from "@/services/dreama-voice-preview";

test("local voice preview reads through the authenticated native resource client and plays a blob URL", async () => {
    const original = apiClient.defaults.adapter;
    const token = apiClient.defaults.headers.common["X-Desktop-Token"];
    const calls: unknown[] = [];
    apiClient.defaults.headers.common["X-Desktop-Token"] = "test-only-desktop-token";
    apiClient.defaults.adapter = async config => {
        calls.push({ method: config.method, url: config.url, token: config.headers.get("X-Desktop-Token"), responseType: config.responseType });
        return { data: new Blob(["sample"], { type: "audio/wav" }), status: 200, statusText: "OK", headers: {}, config };
    };
    try {
        const url = await resolveDreamaVoicePreview("voice-preview-auth-only");
        expect(url.startsWith("blob:")).toBe(true);
        expect(calls).toEqual([{ method: "get", url: "/resources/voice-preview-auth-only/file?proxy=1", token: "test-only-desktop-token", responseType: "blob" }]);
        // Repeated previews reuse the authenticated blob cache, not the raw file URL.
        expect(await resolveDreamaVoicePreview("voice-preview-auth-only")).toBe(url);
        expect(calls.length).toBe(1);
    } finally {
        apiClient.defaults.adapter = original;
        if (token === undefined) delete apiClient.defaults.headers.common["X-Desktop-Token"];
        else apiClient.defaults.headers.common["X-Desktop-Token"] = token;
    }
});

test("failed local voice reads produce an actionable preview message and never return a protected file URL", async () => {
    const original = apiClient.defaults.adapter;
    let calls = 0;
    apiClient.defaults.adapter = async () => { calls++; throw new Error("synthetic forbidden"); };
    try {
        await expect(resolveDreamaVoicePreview("voice-preview-unavailable")).rejects.toThrow("无法读取本机声音，请确认 BeefTV 本机服务可用后重试");
        expect(calls).toBe(1);
        await expect(resolveDreamaVoicePreview("../private")).rejects.toThrow("声音资源标识无效");
        expect(calls).toBe(1);
    } finally { apiClient.defaults.adapter = original; }
});
