import { resourceStorageKey } from "@/services/api/resources";
import { resolveMediaUrl } from "@/services/file-storage";

/** Called only by the explicit preview action. Native audio cannot send the launch token. */
export async function resolveDreamaVoicePreview(resourceId: string): Promise<string> {
    if (!/^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,159}$/.test(resourceId)) throw new Error("声音资源标识无效，请重新选择本机声音");
    const url = await resolveMediaUrl(resourceStorageKey(resourceId));
    // The general resolver can fall back to a protected HTTP URL on failure.
    // Do not give that fallback to an unauthenticated native media element.
    if (!url.startsWith("blob:")) throw new Error("无法读取本机声音，请确认 BeefTV 本机服务可用后重试");
    return url;
}
