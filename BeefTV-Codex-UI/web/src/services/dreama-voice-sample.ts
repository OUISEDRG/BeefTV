import { getResource, getResourceBlob, resourceStorageKey } from "@/services/api/resources";

const MAX_SAMPLE_BYTES = 20 * 1024 * 1024;

/** Only authenticated native resources are accepted; paths and arbitrary URLs never enter this route. */
export async function resolveDreamaVoiceSample(resourceId: string, signal?: AbortSignal): Promise<string> {
    if (!/^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,159}$/.test(resourceId)) throw new Error("音色样本资源标识无效，请重新上传");
    signal?.throwIfAborted();
    const resource = await getResource(resourceId, { signal });
    if (!resource.mimeType?.startsWith("audio/")) throw new Error("音色样本不是音频资源，请重新上传音频文件");
    const blob = await getResourceBlob(resourceStorageKey(resourceId), { signal });
    signal?.throwIfAborted();
    if (!blob?.size || blob.size > MAX_SAMPLE_BYTES) throw new Error("音色样本为空或超过 20 MiB，请重新上传较短的音频");
    const bytes = new Uint8Array(await blob.arrayBuffer());
    signal?.throwIfAborted();
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
    return `data:${resource.mimeType};base64,${btoa(binary)}`;
}
