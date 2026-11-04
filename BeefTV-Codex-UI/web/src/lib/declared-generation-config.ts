import { audioSpeechProfileForConfig, audioSpeechSettingsError } from "@/lib/audio-generation";
import { modelCapabilityConfigFor, videoDurationAllowed } from "@/lib/model-capabilities";
import { videoResolutionComparisonKey } from "@/lib/video-generation-options";
import { modelOptionName, normalizeModelOptionValue, resolveModelChannel, type AiConfig } from "@/stores/use-config-store";

// Validate persisted choices before media preparation. Missing values may use
// capability defaults; explicit stale choices must remain available for repair.
export function declaredGenerationConfigError(config: AiConfig, mode: string, options: { requireVoice?: boolean; hasVoiceSource?: boolean } = {}): string {
    if (config.taskWorkflowProvider === "runninghub") return "";
    const model = config.model;
    if (model?.includes("::") && !normalizeModelOptionValue(model, config.channels)) return `已保存型号「${modelOptionName(model)}」已不可用，请重新选择型号`;
    const channel = resolveModelChannel(config, model);
    if (channel.enabled === false) return "已保存型号的渠道已停用，请重新选择型号";
    const profile = channel.modelProfiles?.find((item) => item.model === modelOptionName(model));
    if (mode === "audio") return audioSpeechSettingsError(audioSpeechProfileForConfig(config), config, options.requireVoice === true && !options.hasVoiceSource);
    if (mode !== "image" && mode !== "video") return "";
    if (!(mode === "image" ? profile?.imageCapabilitiesVersion : profile?.videoCapabilitiesVersion)?.trim()) return "";
    const capability = modelCapabilityConfigFor(config, model);
    const errors: string[] = [];
    const invalid = (label: string, value: string) => errors.push(`${label}「${value}」已不可用，请重新选择`);
    if (mode === "image" && capability.image) {
        const image = capability.image;
        if (config.size && !image.size.allowCustom && !image.size.values.includes("*") && !image.size.values.includes(config.size)) invalid("尺寸或比例", config.size);
        if (config.quality && image.quality.supported && !image.quality.values.includes(config.quality)) invalid("质量或分辨率", config.quality);
    }
    if (mode === "video" && capability.video) {
        const video = capability.video;
        if (config.vquality && video.resolutions.length && !video.resolutions.some((value) => videoResolutionComparisonKey(value) === videoResolutionComparisonKey(config.vquality))) invalid("分辨率", config.vquality);
        if (config.videoSeconds && !videoDurationAllowed(video, Number(config.videoSeconds))) invalid(video.durationSemantics === "additional" ? "新增时长" : "时长", config.videoSeconds);
        if (config.size && video.ratios.length && !video.ratios.includes(config.size)) invalid("比例", config.size);
    }
    return errors.join("；");
}
