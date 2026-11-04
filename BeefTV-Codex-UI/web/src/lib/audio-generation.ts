import { normalizeAudioNumericValue, sanitizeServerAudioCapability, type AudioCapabilityConfig } from "./model-capabilities-audio";
import type { AiConfig } from "@/stores/use-config-store";

export const audioVoiceOptions = [
    { value: "alloy", label: "Alloy" },
    { value: "ash", label: "Ash" },
    { value: "ballad", label: "Ballad" },
    { value: "coral", label: "Coral" },
    { value: "echo", label: "Echo" },
    { value: "fable", label: "Fable" },
    { value: "nova", label: "Nova" },
    { value: "onyx", label: "Onyx" },
    { value: "sage", label: "Sage" },
    { value: "shimmer", label: "Shimmer" },
    { value: "verse", label: "Verse" },
    { value: "marin", label: "Marin" },
    { value: "cedar", label: "Cedar" },
];

export const audioFormatOptions = [
    { value: "mp3", label: "MP3" },
    { value: "wav", label: "WAV" },
    { value: "opus", label: "Opus" },
    { value: "aac", label: "AAC" },
    { value: "flac", label: "FLAC" },
    { value: "pcm", label: "PCM" },
];

// MiniMax native TTS content types: mp3/wav/flac/aac/pcm. Opus is OpenAI-only;
// TokenHub will forward it, but MiniMax does not declare a content type for it.
export const minimaxAudioFormatOptions = audioFormatOptions.filter((item) => item.value !== "opus");

// BeefAPI MiniMax speech: voice is a MiniMax system/cloned ID, not OpenAI alloy.
// Documented example is male-qn-qingse; these are the T2A 系统音色.
export const minimaxSpeechVoiceOptions = [
    { value: "male-qn-qingse", label: "青涩青年" },
    { value: "male-qn-jingying", label: "精英青年" },
    { value: "male-qn-badao", label: "霸道青年" },
    { value: "male-qn-daxuesheng", label: "青年大学生" },
    { value: "female-shaonv", label: "少女" },
    { value: "female-yujie", label: "御姐" },
    { value: "female-chengshu", label: "成熟女性" },
    { value: "female-tianmei", label: "甜美女性" },
    { value: "presenter_male", label: "男主持人" },
    { value: "presenter_female", label: "女主持人" },
    { value: "audiobook_male_1", label: "男有声书 1" },
    { value: "audiobook_male_2", label: "男有声书 2" },
    { value: "audiobook_female_1", label: "女有声书 1" },
    { value: "audiobook_female_2", label: "女有声书 2" },
];

export type AudioSpeechKind = "openai" | "minimax-speech" | "minimax-music" | "declared";

export type AudioSpeechProfile = {
    capability?: AudioCapabilityConfig;
    kind: AudioSpeechKind;
    voices: Array<{ value: string; label: string }>;
    formats: Array<{ value: string; label: string }>;
    defaultVoice: string;
    defaultFormat: string;
    speedMin: number;
    speedMax: number;
    speedOptions: string[];
    showVoice: boolean;
    showSpeed: boolean;
    showPitch: boolean;
    showVolume: boolean;
    showInstructions: boolean;
};

const OPENAI_SPEECH_VOICES = new Set(audioVoiceOptions.map((item) => item.value));

export function audioModelId(model: string) {
    const trimmed = model.trim();
    const separator = trimmed.lastIndexOf("::");
    return (separator >= 0 ? trimmed.slice(separator + 2) : trimmed).toLowerCase();
}

export function audioSpeechKind(model: string): AudioSpeechKind {
    const id = audioModelId(model);
    if (id.includes("minimax-speech")) return "minimax-speech";
    if (id.includes("minimax-music")) return "minimax-music";
    return "openai";
}

export function audioSpeechProfile(model: string | AudioSpeechProfile = ""): AudioSpeechProfile {
    if (typeof model !== "string") return model;
    const kind = audioSpeechKind(model);
    if (kind === "minimax-speech") {
        return {
            kind,
            voices: minimaxSpeechVoiceOptions,
            formats: minimaxAudioFormatOptions,
            defaultVoice: "male-qn-qingse",
            defaultFormat: "mp3",
            speedMin: 0.5,
            speedMax: 2,
            speedOptions: ["0.5", "0.75", "1", "1.25", "1.5", "2"],
            showVoice: true,
            showSpeed: true,
            showPitch: false,
            showVolume: false,
            showInstructions: false,
        };
    }
    if (kind === "minimax-music") {
        return {
            kind,
            voices: [],
            formats: audioFormatOptions.filter((item) => item.value === "mp3"),
            defaultVoice: "",
            defaultFormat: "mp3",
            speedMin: 1,
            speedMax: 1,
            speedOptions: ["1"],
            showVoice: false,
            showSpeed: false,
            showPitch: false,
            showVolume: false,
            showInstructions: false,
        };
    }
    return {
        kind,
        voices: audioVoiceOptions,
        formats: audioFormatOptions,
        defaultVoice: "alloy",
        defaultFormat: "mp3",
        speedMin: 0.25,
        speedMax: 4,
        speedOptions: ["0.75", "1", "1.25", "1.5"],
        showVoice: true,
        showSpeed: true,
        showPitch: false,
        showVolume: false,
        showInstructions: true,
    };
}

export function normalizeAudioVoiceValue(value: string, model?: string | AudioSpeechProfile) {
    const profile = audioSpeechProfile(model);
    if (profile.kind === "declared") return String(value || "").trim() || profile.defaultVoice;
    if (!profile.showVoice) return "";
    const trimmed = String(value || "").trim();
    if (profile.voices.some((item) => item.value === trimmed)) return trimmed;
    if (profile.kind === "minimax-speech") {
        if (!trimmed || OPENAI_SPEECH_VOICES.has(trimmed) || trimmed === "中文") return profile.defaultVoice;
        return trimmed;
    }
    return OPENAI_SPEECH_VOICES.has(trimmed) ? trimmed : profile.defaultVoice;
}

export function normalizeAudioFormatValue(value: string, model?: string | AudioSpeechProfile) {
    const profile = audioSpeechProfile(model);
    if (profile.kind === "declared") return value?.trim() ? value : profile.defaultFormat;
    const trimmed = String(value || "")
        .trim()
        .toLowerCase();
    return profile.formats.some((item) => item.value === trimmed) ? trimmed : profile.defaultFormat;
}

export function normalizeAudioSpeedValue(value: string, model?: string | AudioSpeechProfile) {
    const profile = audioSpeechProfile(model);
    if (profile.capability) return normalizeAudioNumericValue(value, profile.capability.speed);
    if (!String(value ?? "").trim()) return "1";
    const speed = Number(value);
    if (!Number.isFinite(speed) || speed <= 0) return "1";
    return String(Math.max(profile.speedMin, Math.min(profile.speedMax, Number(speed.toFixed(2)))));
}

export function normalizeAudioPitchValue(value: string, profile?: AudioSpeechProfile) {
    if (profile?.capability) return normalizeAudioNumericValue(value, profile.capability.pitch);
    const pitch = Number(value);
    if (!Number.isFinite(pitch)) return "0";
    return String(Math.max(-12, Math.min(12, Number(pitch.toFixed(2)))));
}

export function normalizeAudioVolumeValue(value: string, profile?: AudioSpeechProfile) {
    if (profile?.capability) return normalizeAudioNumericValue(value, profile.capability.volume);
    const volume = Number(value);
    if (!Number.isFinite(volume)) return "1";
    return String(Math.max(0, Math.min(1, Number(volume.toFixed(2)))));
}

export function audioVoiceLabel(value: string, model?: string | AudioSpeechProfile) {
    const profile = audioSpeechProfile(model);
    const voice = normalizeAudioVoiceValue(value, model);
    return profile.voices.find((item) => item.value === voice)?.label || voice;
}

export function audioFormatLabel(value: string, model?: string | AudioSpeechProfile) {
    const format = normalizeAudioFormatValue(value, model);
    const profile = audioSpeechProfile(model);
    return profile.formats.find((item) => item.value === format)?.label || format;
}

export function audioSpeedLabel(value: string, model?: string | AudioSpeechProfile) {
    return `${normalizeAudioSpeedValue(value, model)}x`;
}

export function audioPitchLabel(value: string, profile?: AudioSpeechProfile) {
    const pitch = normalizeAudioPitchValue(value, profile);
    return `${Number(pitch) > 0 ? "+" : ""}${pitch}`;
}

export function audioVolumeLabel(value: string, profile?: AudioSpeechProfile) {
    return `${Math.round(Number(normalizeAudioVolumeValue(value, profile)) * 100)}%`;
}

export function resolveAudioSpeechSettings(
    model: string | AudioSpeechProfile,
    values: {
        audioVoice?: string;
        audioFormat?: string;
        audioSpeed?: string;
        audioPitch?: string;
        audioVolume?: string;
        audioInstructions?: string;
    },
) {
    const profile = audioSpeechProfile(model);
    return {
        audioVoice: normalizeAudioVoiceValue(values.audioVoice || "", model),
        audioFormat: normalizeAudioFormatValue(values.audioFormat || "", model),
        audioSpeed: normalizeAudioSpeedValue(values.audioSpeed || "", model),
        audioPitch: profile.capability || profile.showPitch ? normalizeAudioPitchValue(values.audioPitch || "", profile) : "0",
        audioVolume: profile.capability || profile.showVolume ? normalizeAudioVolumeValue(values.audioVolume || "", profile) : "1",
        audioInstructions: profile.capability || profile.showInstructions ? String(values.audioInstructions || "") : "",
    };
}

export function audioSettingsSummary(config: { model?: string; audioModel?: string; channels?: AiConfig["channels"]; audioVoice?: string; audioFormat?: string; audioSpeed?: string; audioPitch?: string; audioVolume?: string }) {
    const profile = audioSpeechProfileForConfig(config);
    const model = profile;
    const parts: string[] = [];
    if (profile.showVoice) parts.push(audioVoiceLabel(config.audioVoice || "", model));
    if (profile.formats.length) parts.push(audioFormatLabel(config.audioFormat || "", model));
    if (profile.kind === "minimax-music") parts.push("256 kbps");
    if (profile.showSpeed) parts.push(audioSpeedLabel(config.audioSpeed || "", model));
    if (profile.showPitch) parts.push(`音调${audioPitchLabel(config.audioPitch || "", profile)}`);
    if (profile.showVolume) parts.push(`音量${audioVolumeLabel(config.audioVolume || "", profile)}`);
    return parts.join(" · ") || "使用模型默认设置";
}

export function buildAudioSpeechRequest(
    config: {
        model: string;
        audioVoice?: string;
        audioFormat?: string;
        audioSpeed?: string;
        audioInstructions?: string;
        channels?: AiConfig["channels"];
    },
    prompt: string,
) {
    const model = config.model.trim();
    const profile = audioSpeechProfileForConfig(config);
    const settings = resolveAudioSpeechSettings(profile, config);
    const error = audioSpeechSettingsError(profile, settings, true);
    if (error) throw new Error(error);
    const payload: Record<string, unknown> = {
        model,
        input: prompt,
    };
    if (profile.formats.length) payload.response_format = settings.audioFormat;
    if (profile.showVoice) payload.voice = settings.audioVoice;
    if (profile.showSpeed) payload.speed = Number(settings.audioSpeed);
    if (profile.showPitch) payload.pitch = Number(settings.audioPitch);
    if (profile.showVolume) payload.volume = Number(settings.audioVolume);
    if (profile.kind === "minimax-music") {
        payload.metadata = { audio_setting: { sample_rate: 44100, bitrate: 256000, format: "mp3" } };
    }
    const instructions = settings.audioInstructions.trim();
    if (profile.showInstructions && instructions) payload.instructions = instructions;
    return payload;
}

export function audioSpeechSettingsError(profile: AudioSpeechProfile, values: Partial<Pick<AiConfig, "audioVoice" | "audioFormat" | "audioSpeed" | "audioPitch" | "audioVolume" | "audioInstructions">>, requireVoice = false): string {
    const capability = profile.capability;
    if (!capability) return "";
    const errors: string[] = [];
    const voice = values.audioVoice?.trim() || capability.voices.default;
    if (capability.voices.supported) {
        if (requireVoice && !voice) errors.push("请选择音色或绑定人物音色后再生成");
        else if (voice && !capability.voices.allowCustom && !capability.voices.values.some((item) => item.value === voice)) errors.push(`音色「${voice}」已不可用，请重新选择`);
    } else if (voice && voice !== capability.voices.default) errors.push(`音色「${voice}」已不支持修改，请清除该音色设置`);
    const format = values.audioFormat || capability.formats.default;
    if (format && (capability.formats.supported ? !capability.formats.values.includes(format) : format !== capability.formats.default)) errors.push(`音频格式「${format}」已不可用，请重新选择`);
    for (const [key, label, field] of [["audioSpeed", "语速", capability.speed], ["audioPitch", "声调", capability.pitch], ["audioVolume", "音量", capability.volume]] as const) {
        const raw = values[key];
        if (!raw?.trim()) continue;
        const numeric = Number(raw);
        if (!field.supported) {
            if (numeric !== field.default) errors.push(`${label}「${raw}」已不支持修改，请恢复为 ${field.default}`);
            continue;
        }
        const steps = (numeric - field.min) / field.step;
        if (!Number.isFinite(numeric) || numeric < field.min || numeric > field.max || Math.abs(steps - Math.round(steps)) > 1e-6) errors.push(`${label}「${raw}」无效，请使用 ${field.min}–${field.max}、步长 ${field.step} 的值`);
    }
    if (!capability.instructions.supported && values.audioInstructions?.trim()) errors.push("声音指令已不支持，请清除该设置");
    return errors.join("；");
}

export function audioSpeechProfileForConfig(config: { model?: string; audioModel?: string; channels?: AiConfig["channels"] }): AudioSpeechProfile {
    const model = config.model || config.audioModel || "";
    const separator = model.indexOf("::");
    const name = separator < 0 ? model : model.slice(separator + 2);
    const channel = config.channels?.find((item) => separator < 0 ? item.models.includes(name) : item.id === model.slice(0, separator));
    const declared = channel?.modelProfiles?.find((item) => item.model === name);
    if (!declared?.audioCapabilitiesVersion?.trim()) return audioSpeechProfile(model);
    const capability = sanitizeServerAudioCapability(declared.capabilityConfig?.audio);
    if (!capability) throw new Error("当前音频模型能力无效，请刷新模型目录");
    const speedOptions = Array.from(new Set([capability.speed.min, capability.speed.default, capability.speed.max])).map(String);
    return {
        kind: "declared", capability,
        voices: capability.voices.supported ? capability.voices.values : [],
        formats: capability.formats.supported ? capability.formats.values.map((value) => ({ value, label: value.toUpperCase() })) : [],
        defaultVoice: capability.voices.default, defaultFormat: capability.formats.default,
        speedMin: capability.speed.min, speedMax: capability.speed.max, speedOptions,
        showVoice: capability.voices.supported, showSpeed: capability.speed.supported,
        showPitch: capability.pitch.supported, showVolume: capability.volume.supported,
        showInstructions: capability.instructions.supported,
    };
}

export function audioMimeType(format: string) {
    if (format === "wav") return "audio/wav";
    if (format === "opus") return "audio/opus";
    if (format === "aac") return "audio/aac";
    if (format === "flac") return "audio/flac";
    if (format === "pcm") return "audio/pcm";
    return "audio/mpeg";
}
