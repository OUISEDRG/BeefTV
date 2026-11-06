import type { AiConfig } from "@/stores/use-config-store";
import { modelCapabilityConfigFor } from "@/lib/model-capabilities";
import type { ReferenceImage } from "@/types/image";
import type { CanvasNodeData } from "@/types/canvas";
import { ownedResourceIdFromMediaRef, resourceStorageKey } from "@/services/api/resources";
import { resolveImageEditSource, uploadImage } from "@/services/image-storage";
import { emotionGenerationSize, resolveEmotionEditPlan, type CanvasEmotionEditRegion } from "./canvas-emotion";

export async function freezeImageEditSource(source: ReferenceImage) {
    if (source.storageKey) return source;
    const resource = ownedResourceIdFromMediaRef(undefined, source.dataUrl);
    if (resource) return { ...source, storageKey: resourceStorageKey(resource) };
    const uploaded = await uploadImage(await resolveImageEditSource(undefined, source.dataUrl));
    return { ...source, storageKey: uploaded.storageKey, dataUrl: uploaded.url, type: uploaded.mimeType };
}

export function savedImageEditSource(references: string[] | undefined, sourceNodeId: string, nodes: CanvasNodeData[]): ReferenceImage {
    const saved = references?.[0];
    const source = nodes.find(node => node.id === sourceNodeId)?.metadata;
    // Older nodes may only identify their source by node id. A frozen reference
    // takes precedence even if the original card was replaced or deleted.
    const key = saved && !/^(?:https?:|blob:|data:|\/)/i.test(saved) ? saved : undefined;
    const storageKey = saved ? key : source?.storageKey;
    const dataUrl = saved ? (key ? "" : saved) : source?.content || "";
    if (!storageKey && !dataUrl) throw new Error("局部编辑原图已丢失，请重新选择原图");
    return { id: sourceNodeId, name: "original-image", type: "image/png", storageKey, dataUrl };
}

export function emotionGenerationSettings(config: AiConfig, region: CanvasEmotionEditRegion) {
    const profile = modelCapabilityConfigFor(config, config.model).image;
    if (!profile || profile.references.maxImages < 2) throw new Error("表情编辑需要支持两张参考图的图片模型，请更换图片模型");
    const pixels = emotionGenerationSize(region);
    const ratio = pixels === "1536x1024" ? "3:2" : pixels === "1024x1536" ? "2:3" : "1:1";
    const requestedSize = profile.size.parameter === "aspect_ratio" ? ratio : pixels;
    const size = profile.size.values.includes(requestedSize) || (profile.size.parameter === "size" && profile.size.allowCustom)
        ? requestedSize : profile.size.default;
    const quality = profile.quality.values.includes(config.quality) && config.quality !== "auto" ? config.quality
        : profile.quality.values.includes("high") ? "high" : profile.quality.default;
    return { generationConfig: { ...config, count: "1", size, quality }, editPlan: resolveEmotionEditPlan(profile.references.maskSupported) };
}

export function maskEditNeedsGuide(config: AiConfig) {
    const profile = modelCapabilityConfigFor(config, config.model).image;
    if (profile?.references.maskSupported && profile.references.maxImages >= 1) return false;
    if (profile && profile.references.maxImages >= 2) return true;
    throw new Error("局部重绘需要支持蒙版或两张参考图的图片模型，请在局部重绘设置中更换模型");
}

async function loadEditImage(url: string) {
    const response = await fetch(url, { credentials: "same-origin" });
    if (!response.ok) throw new Error("局部编辑素材读取失败");
    return createImageBitmap(await response.blob());
}

function editSurface(width: number, height: number) {
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("浏览器无法处理局部重绘选区");
    return { canvas, context };
}

export async function maskEditInputs(source: ReferenceImage, mask: ReferenceImage, useGuide: boolean, prompt: string) {
    if (!useGuide) return { referenceImages: [source], mask, prompt: `只修改蒙版透明区域，其他区域保持不变。${prompt}` };
    const selection = await loadEditImage(mask.dataUrl);
    try {
        const { canvas, context } = editSurface(selection.width, selection.height);
        context.fillStyle = "black";
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.drawImage(selection, 0, 0);
        const guide = { id: mask.id, name: "selection-guide.png", type: "image/png", dataUrl: canvas.toDataURL("image/png") };
        return { referenceImages: [source, guide], prompt: `编辑第一张原图。第二张是与原图对齐的选区图，黑色区需要修改，白色区保留。返回与第一张原图构图、取景、比例一致的完整图。修改要求：${prompt}` };
    } finally { selection.close(); }
}

export async function compositeMaskedResult(sourceUrl: string, generatedUrl: string, maskUrl: string) {
    const source = await loadEditImage(sourceUrl);
    try {
        const { canvas, context } = editSurface(source.width, source.height);
        const generated = await loadEditImage(generatedUrl);
        try { context.drawImage(generated, 0, 0, canvas.width, canvas.height); }
        finally { generated.close(); }
        const mask = await loadEditImage(maskUrl);
        try {
            context.globalCompositeOperation = "destination-out";
            context.drawImage(mask, 0, 0, canvas.width, canvas.height);
        } finally { mask.close(); }
        context.globalCompositeOperation = "destination-over";
        context.drawImage(source, 0, 0);
        return canvas.toDataURL("image/png");
    } finally { source.close(); }
}
