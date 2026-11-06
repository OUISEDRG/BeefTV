import type { NodeGenerationContext } from "@/components/canvas/canvas-node-generation";
import { parseDreamaVoiceBinding } from "@/lib/dreama-voice";
import { resolveModelRequestConfig, type AiConfig } from "@/stores/use-config-store";
import type { DreamaVoiceBinding } from "@/types/dreama-voice";
import type { ReferenceImage } from "@/types/image";

export type DreamaCharacterVoice = { image_index: number; name: string; voice_id?: string; sampleResourceId?: string };
type VoiceContext = Pick<NodeGenerationContext, "referenceImages" | "referenceAudios" | "characterReferences" | "imageCount" | "dreamaAudioVoiceBinding">;

function binding(value: unknown): DreamaVoiceBinding {
    const parsed = parseDreamaVoiceBinding(value);
    if (!parsed) throw new Error("人物图片的音色绑定已失效，请重新绑定音色后生成");
    return parsed;
}

/** Apply only to the request snapshot. The image node, its content and edges stay intact. */
export function prepareDreamaAudioIdentity<T extends VoiceContext>(context: T, mode: string): T {
    if (mode !== "audio") return context;
    const bound = context.referenceImages.filter(image => image.dreamaVoiceBinding !== undefined);
    if (!bound.length) return context;
    if (bound.length !== 1 || context.dreamaAudioVoiceBinding || context.characterReferences.length) {
        throw new Error("独立配音一次只能连接一张已绑定音色的人物图片，请移除多余人物引用");
    }
    if (context.referenceAudios.length || context.referenceImages.length !== 1) {
        throw new Error("绑定人物配音不能同时连接其他参考图片或音频，请仅保留该人物图片");
    }
    return { ...context, referenceImages: [], imageCount: 0, dreamaAudioVoiceBinding: binding(bound[0]!.dreamaVoiceBinding) };
}

export function dreamaVoiceProtocol(config: AiConfig, mode: string): string {
    const protocol = resolveModelRequestConfig(config, config.model).interfaceType;
    if (mode === "video" && (protocol === "dreama-videos-async" || protocol === "dreama-extension-async")) return protocol;
    if (mode === "audio" && protocol === "dreama-audio-async") return protocol;
    throw new Error("当前模型不支持人物图片音色绑定，请选择已接入的剧梦视频、视频延长或配音模型");
}

/** The final array order is the order sent to the provider, after reference-token compilation. */
export async function buildDreamaCharacterVoices(
    images: ReferenceImage[],
    audioIdentity: DreamaVoiceBinding | undefined,
    resolveSample: (resourceId: string) => Promise<string>,
): Promise<DreamaCharacterVoice[]> {
    const inputs = audioIdentity
        ? [{ index: 1, value: audioIdentity }]
        : images.flatMap((image, index) => image.dreamaVoiceBinding === undefined ? [] : [{ index: index + 1, value: image.dreamaVoiceBinding }]);
    if (!inputs.length) return [];
    if (audioIdentity && images.length) throw new Error("绑定人物配音不接受图片参考，请重新连接人物图片");
    const seen = new Set<string>();
    return Promise.all(inputs.map(async ({ index, value }) => {
        const parsed = binding(value);
        // Reusing one image twice is ambiguous even when its saved binding is identical.
        const imageId = audioIdentity ? "audio-identity" : images[index - 1]!.id;
        if (seen.has(imageId)) throw new Error("同一人物图片被重复引用，请只保留一个引用");
        seen.add(imageId);
        const result: DreamaCharacterVoice = { image_index: index, name: parsed.name };
        if (parsed.source === "catalog") return { ...result, voice_id: parsed.voiceId! };
        const sampleResourceId = await resolveSample(parsed.sampleResourceId!);
        if (sampleResourceId !== parsed.sampleResourceId || !/^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,159}$/.test(sampleResourceId)) {
            throw new Error("音色样本必须是已保存的音频资源，请重新上传音频样本");
        }
        return { ...result, sampleResourceId };
    }));
}

export async function prepareDreamaVoiceProviderOptions(
    context: Pick<NodeGenerationContext, "referenceImages" | "referenceAudios" | "dreamaAudioVoiceBinding">,
    config: AiConfig,
    mode: string,
    resolveSample: (resourceId: string) => Promise<string>,
): Promise<Record<string, Record<string, unknown>> | undefined> {
    if (mode !== "video" && mode !== "audio") return undefined;
    if (!context.dreamaAudioVoiceBinding && !context.referenceImages.some(image => image.dreamaVoiceBinding !== undefined)) return undefined;
    const protocol = dreamaVoiceProtocol(config, mode);
    if (mode === "audio" && context.referenceAudios.length) throw new Error("绑定人物配音不能同时连接参考音频");
    const voices = await buildDreamaCharacterVoices(context.referenceImages, context.dreamaAudioVoiceBinding, resolveSample);
    return { [protocol]: { character_voices: voices } };
}
