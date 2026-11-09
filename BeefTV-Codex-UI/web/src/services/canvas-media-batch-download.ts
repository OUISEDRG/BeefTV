import { buildBatchDownloadFilename } from "@/lib/canvas/canvas-media-download";
import { ownedResourceIdFromMediaRef, resourceStorageKey } from "@/services/api/resources";
import { isWailsNativeShell, saveOwnedOrBrowserBlob } from "@/services/desktop-media-save";
import { getMediaBlob } from "@/services/file-storage";
import { getImageBlob } from "@/services/image-storage";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";

const BROWSER_DOWNLOAD_INTERVAL_MS = 120;

export async function batchDownloadCanvasMediaNodes(nodes: CanvasNodeData[]) {
    const files = nodes.map((node, index) => ({
        node,
        name: buildBatchDownloadFilename(node, index, nodes.length),
        resourceId: ownedResourceIdFromMediaRef(node.metadata?.storageKey?.trim(), node.metadata?.content?.trim()),
    }));
    if (isWailsNativeShell()) {
        const entries = files.filter((file) => file.resourceId).map(({ name, resourceId }) => ({ name, resourceId }));
        if (entries.length === 0) return { cancelled: false, downloaded: 0, failed: files.length };
        const save = window.go?.main?.DesktopApp?.SaveOwnedMediaBatch;
        if (!save) throw new Error("当前应用还不能批量保存素材");
        const result = await save(entries);
        return result.cancelled ? result : { ...result, failed: result.failed + files.length - entries.length };
    }

    let downloaded = 0;
    let failed = 0;
    for (const { node, name, resourceId } of files) {
        try {
            const blob = await readBrowserMedia(node, resourceId);
            if (!blob?.size) throw new Error("没有可导出的文件");
            await saveOwnedOrBrowserBlob(name, blob);
            downloaded += 1;
            if (downloaded + failed < files.length) {
                await new Promise<void>((resolve) => setTimeout(resolve, BROWSER_DOWNLOAD_INTERVAL_MS));
            }
        } catch {
            failed += 1;
        }
    }
    return { cancelled: false, downloaded, failed };
}

async function readBrowserMedia(node: CanvasNodeData, resourceId: string) {
    const content = node.metadata?.content?.trim();
    const key = resourceId ? resourceStorageKey(resourceId) : node.metadata?.storageKey?.trim();
    if (key) {
        const readStoredBlob = node.type === CanvasNodeType.Image ? getImageBlob : getMediaBlob;
        const stored = await readStoredBlob(key).catch(() => null);
        if (stored?.size) return stored;
    }
    if (!content || !/^(?:https?:\/\/|blob:|data:|\/)/i.test(content)) return null;
    const response = await fetch(content, { credentials: "same-origin" });
    return response.ok ? response.blob() : null;
}
