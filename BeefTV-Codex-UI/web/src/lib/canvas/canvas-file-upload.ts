import { fitNodeSize } from "@/lib/canvas/canvas-node-size";
import { NODE_DEFAULT_SIZE } from "@/constant/canvas";
import { CanvasNodeType, type CanvasNodeData, type Position } from "@/types/canvas";

export const CANVAS_UPLOAD_ACCEPT = "image/*,video/*,.mp4,.mov,.m4v,.mkv,.webm,.avi,audio/*,.mp3,.wav,text/plain,text/markdown,.txt,.md,.markdown";
export function isTextUploadFile(file: Pick<File, "name" | "type">) {
    return /\.(txt|md|markdown)$/i.test(file.name) || file.type === "text/plain" || file.type === "text/markdown";
}
export function uploadNodeType(file: Pick<File, "name" | "type">) {
    if (file.type.startsWith("image/")) return CanvasNodeType.Image;
    if (file.type.startsWith("video/") || /\.(mp4|mov|m4v|mkv|webm|avi)$/i.test(file.name)) return CanvasNodeType.Video;
    if (file.type.startsWith("audio/") || /\.(mp3|wav)$/i.test(file.name)) return CanvasNodeType.Audio;
    if (isTextUploadFile(file)) return CanvasNodeType.Text;
    return null;
}
export function uploadPercent(loaded: number, total: number): number | undefined {
    if (!Number.isFinite(loaded) || !Number.isFinite(total) || total <= 0) return undefined;
    return Math.round(Math.min(1, Math.max(0, loaded / total)) * 100);
}
export function uploadVeilOpacity(percent = 0) {
    return 1 - Math.min(100, Math.max(0, percent)) / 100 * 0.9;
}

export async function createFileUploadPlaceholder(id: string, file: File, position: Position) {
    const type = uploadNodeType(file);
    if (!type) throw new Error("请选择图片、视频、音频或 TXT / Markdown 文件");
    if (type === CanvasNodeType.Image) return createImageUploadPlaceholder(id, file, position, await readUploadImageSize(file));
    // 浏览器能否解码不决定能否导入；尺寸与封面由上传后的媒体信息补齐。
    const size = NODE_DEFAULT_SIZE[type];
    return {
        ...size, id, type, title: file.name,
        position: { x: position.x - size.width / 2, y: position.y - size.height / 2 },
        metadata: { fileUpload: "uploading", bytes: file.size, mimeType: file.type },
    } satisfies CanvasNodeData;
}

// 不使用 readImageMeta 的默认尺寸兜底：上传占位必须来自实际解码尺寸。
export async function readUploadImageSize(file: File): Promise<{ width: number; height: number }> {
    const url = URL.createObjectURL(file);
    try {
        return await readImageSourceSize(url);
    } finally {
        URL.revokeObjectURL(url);
    }
}

export async function readImageSourceSize(url: string): Promise<{ width: number; height: number }> {
    if (!url) throw new Error("素材图片地址不可用");
    const image = new Image();
    image.src = url;
    await image.decode();
    if (!image.naturalWidth || !image.naturalHeight) throw new Error("无法读取图片尺寸，请重新选择图片");
    return { width: image.naturalWidth, height: image.naturalHeight };
}

/** Do not let a slow upload overwrite geometry the user changed while it was in flight. */
export function fitCanvasUploadedVideoNode(current: CanvasNodeData, placeholder: CanvasNodeData, naturalWidth: number, naturalHeight: number): CanvasNodeData {
    if (current.metadata?.manualSize || current.metadata?.freeResize || current.metadata?.locked || current.metadata?.workflowSlot) return current;
    if (![naturalWidth, naturalHeight].every((value) => Number.isFinite(value) && value > 0)) return current;
    const same = (a: number, b: number) => Math.abs(a - b) < 0.01;
    if (!same(current.position.x, placeholder.position.x) || !same(current.position.y, placeholder.position.y)
        || !same(current.width, placeholder.width) || !same(current.height, placeholder.height)) return current;
    const size = fitNodeSize(naturalWidth, naturalHeight);
    return { ...current, ...size, position: {
        x: current.position.x + (current.width - size.width) / 2,
        y: current.position.y + (current.height - size.height) / 2,
    } };
}

/** Server video metadata can resize a placeholder around its center. User edits take precedence. */
export function canRelayoutCanvasUploadBatch(prepared: CanvasNodeData[], current: CanvasNodeData[]) {
    const byId = new Map(current.map((node) => [node.id, node]));
    const same = (a: number, b: number) => Math.abs(a - b) < 0.01;
    return prepared.every((before) => {
        const after = byId.get(before.id);
        if (!after || after.metadata?.locked || after.metadata?.manualSize || after.metadata?.freeResize || after.metadata?.workflowSlot) return false;
        if (after.type === CanvasNodeType.Video && after.metadata?.naturalWidth && after.metadata?.naturalHeight) {
            return same(before.position.x + before.width / 2, after.position.x + after.width / 2)
                && same(before.position.y + before.height / 2, after.position.y + after.height / 2);
        }
        return same(before.position.x, after.position.x) && same(before.position.y, after.position.y)
            && same(before.width, after.width) && same(before.height, after.height);
    });
}

export function createImageUploadPlaceholder(id: string, file: Pick<File, "name" | "size" | "type">, position: Position, naturalSize: { width: number; height: number }): CanvasNodeData {
    const size = fitNodeSize(naturalSize.width, naturalSize.height);
    return {
        id, type: CanvasNodeType.Image, title: file.name,
        position: { x: position.x - size.width / 2, y: position.y - size.height / 2 },
        ...size,
        metadata: { fileUpload: "uploading", naturalWidth: naturalSize.width, naturalHeight: naturalSize.height, bytes: file.size, mimeType: file.type },
    };
}

export function interruptFileUpload(node: CanvasNodeData): CanvasNodeData {
    if (node.metadata?.fileUpload !== "uploading") return node;
    return { ...node, metadata: { ...node.metadata, fileUpload: "error", fileUploadProgress: undefined, errorDetails: "上传已中断，请重新选择文件上传" } };
}
