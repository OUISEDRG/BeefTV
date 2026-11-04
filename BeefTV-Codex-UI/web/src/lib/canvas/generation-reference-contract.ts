import type { ReferenceImage } from "@/types/image";
import type { ReferenceAudio, ReferenceVideo } from "@/types/media";
import { assertCanvasImageReferenceTable, type CanvasImageTableReference } from "@/lib/canvas/canvas-reference-table";

export type BoundMediaMention = {
    token: string;
    kind: "image" | "video" | "audio";
    referenceId: string;
};

export type CharacterReferenceBinding = {
    role: number;
    name: string;
    imageIds: string[];
    audioIds: string[];
};

type ReferenceContext = {
    prompt: string;
    referenceImages: ReferenceImage[];
    referenceVideos: ReferenceVideo[];
    referenceAudios: ReferenceAudio[];
    boundMediaMentions?: BoundMediaMention[];
    imageReferenceTableReferences?: CanvasImageTableReference[];
    characterReferenceBindings?: CharacterReferenceBinding[];
};

/** Render only bound tokens. Free text and legacy positional references keep their original meaning. */
export function compileBoundMediaReferences<T extends ReferenceContext>(context: T, firstMentionOrder: boolean, expandCharacters = false): T {
    const bindings = new Map((context.boundMediaMentions || []).map((binding) => [binding.token, binding]));
    const hasLegacySlots = /@(图片|视频|音频|绘图)\d+/u.test(context.prompt);
    const tokens = expandCharacters ? /@\[(?:node|asset):[^\]]+\]|@角色\d+/gu : /@\[(?:node|asset):[^\]]+\]/gu;
    const characters = new Map((context.characterReferenceBindings || []).map(binding => [`@角色${binding.role}`, binding]));
    const occurrences = Array.from(context.prompt.matchAll(tokens));
    const mentions: BoundMediaMention[] = occurrences.flatMap((match) => {
        if (match[0].startsWith("@角色")) {
            const character = characters.get(match[0]);
            if (!character || !character.imageIds.length && !character.audioIds.length) throw new Error(`引用 ${match[0]} 没有可用的角色素材`);
            return [...character.imageIds.map(referenceId => ({ token: match[0], kind: "image" as const, referenceId })), ...character.audioIds.map(referenceId => ({ token: match[0], kind: "audio" as const, referenceId }))];
        }
        const binding = bindings.get(match[0]);
        if (!binding) throw new Error(`引用 ${match[0]} 没有对应的素材，请重新选择后再生成`);
        return [binding];
    });
    const reorder = <R extends { id: string }>(items: R[], kind: BoundMediaMention["kind"]): R[] => {
        if (!firstMentionOrder || hasLegacySlots) return items;
        const byId = new Map(items.map((item) => [item.id, item]));
        const ids = [...new Set(mentions.filter((mention) => mention.kind === kind).map((mention) => mention.referenceId))];
        return [...ids.map((id) => {
            const item = byId.get(id);
            if (!item) throw new Error("引用的素材已不可用，请重新选择后再生成");
            return item;
        }), ...items.filter((item) => !ids.includes(item.id))];
    };
    const referenceImages = reorder(context.referenceImages, "image");
    const referenceVideos = reorder(context.referenceVideos, "video");
    const referenceAudios = reorder(context.referenceAudios, "audio");
    const labels = new Map<string, string>();
    const boundImageSlots = new Map<string, number>();
    const arrays = { image: referenceImages, video: referenceVideos, audio: referenceAudios };
    const names = { image: "图片", video: "视频", audio: "音频" };
    mentions.forEach((binding) => {
        const index = arrays[binding.kind].findIndex((item) => item.id === binding.referenceId);
        if (index < 0) throw new Error("引用的素材已不可用，请重新选择后再生成");
        if (!binding.token.startsWith("@角色")) {
            labels.set(binding.token, `@${names[binding.kind]}${index + 1}`);
            if (binding.kind === "image") boundImageSlots.set(binding.token.slice(1), index + 1);
        }
    });
    if (expandCharacters) characters.forEach((character, token) => {
        const parts = [...character.imageIds.map(id => ({ id, kind: "image" as const })), ...character.audioIds.map(id => ({ id, kind: "audio" as const }))];
        labels.set(token, parts.map(({ id, kind }) => {
            const index = arrays[kind].findIndex(item => item.id === id);
            if (index < 0) throw new Error("角色参考素材已不可用，请重新选择后再生成");
            return `@${names[kind]}${index + 1}`;
        }).join("、"));
    });
    const tableReferencesById = new Map((context.imageReferenceTableReferences || []).map((image) => [image.id, image]));
    // Check the original table's explicit bindings against the final upload order.
    // Saved labels/names remain useful here; synthetic file names cannot replace them.
    assertCanvasImageReferenceTable(context.prompt, referenceImages.map((image, index) => tableReferencesById.get(image.id) || {
        title: image.name, label: `图片${index + 1}`,
    }), boundImageSlots);
    return {
        ...context,
        prompt: context.prompt.replace(tokens, (token) => labels.get(token)!),
        referenceImages, referenceVideos, referenceAudios,
    };
}

export function renderCharacterReferenceBindings(bindings: CharacterReferenceBinding[], context: ReferenceContext) {
    const labels = (ids: string[], items: { id: string }[], kind: string) => ids.map((id) => {
        const index = items.findIndex((item) => item.id === id);
        if (index < 0) throw new Error("角色参考素材已不可用，请重新选择后再生成");
        return `@${kind}${index + 1}`;
    });
    return `【角色与参考素材】\n${bindings.map((binding) => {
        const images = labels(binding.imageIds, context.referenceImages, "图片");
        const audios = labels(binding.audioIds, context.referenceAudios, "音频");
        return [
            `@角色${binding.role} 与「${binding.name}」指同一角色。`,
            images.length && `该角色外观参考 ${images.join("、")}，这些图片均属于同一角色。`,
            audios.length && `该角色音色参考 ${audios.join("、")}。`,
        ].filter(Boolean).join("");
    }).join("\n")}`;
}
