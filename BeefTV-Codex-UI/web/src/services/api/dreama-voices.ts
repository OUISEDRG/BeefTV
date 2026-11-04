import { dreamaVoiceCatalogChannel, parseDreamaVoiceCatalog } from "@/lib/dreama-voice";
import { http } from "@/services/api/request";
import type { AiConfig } from "@/stores/use-config-store";

export async function listDreamaVoices(config: AiConfig, signal?: AbortSignal) {
    const channel = dreamaVoiceCatalogChannel(config);
    if (!channel) throw new Error("未找到已启用的本机剧梦音频渠道，请先在模型设置中启用");
    const result = await http.get<unknown>("/workspace/model-config/voices", { params: { channelId: channel.id }, signal, timeout: 35_000 });
    return parseDreamaVoiceCatalog(result);
}
