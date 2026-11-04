export type AudioNumericCapability = { supported: boolean; min: number; max: number; step: number; default: number };
export type AudioCapabilityConfig = {
    voices: { supported: boolean; values: Array<{ value: string; label: string }>; default: string; allowCustom?: boolean };
    formats: { supported: boolean; values: string[]; default: string };
    speed: AudioNumericCapability;
    pitch: AudioNumericCapability;
    volume: AudioNumericCapability;
    instructions: { supported: boolean };
    references: { promptMaxChars: number };
};

export function sanitizeServerAudioCapability(value: unknown): AudioCapabilityConfig | null {
    if (!value || typeof value !== "object") return null;
    const audio = value as AudioCapabilityConfig;
    if (!audio.voices || typeof audio.voices.supported !== "boolean" || typeof audio.voices.default !== "string" || !Array.isArray(audio.voices.values)) return null;
    if (audio.voices.values.some((voice) => !voice || typeof voice.value !== "string" || !voice.value.trim() || typeof voice.label !== "string")) return null;
    if (audio.voices.default !== "" && audio.voices.supported && !audio.voices.allowCustom && !audio.voices.values.some((voice) => voice.value === audio.voices.default)) return null;
    if (!audio.formats || typeof audio.formats.supported !== "boolean" || typeof audio.formats.default !== "string" || !Array.isArray(audio.formats.values) || audio.formats.values.some((format) => typeof format !== "string" || !format.trim())) return null;
    if (audio.formats.supported && !audio.formats.values.includes(audio.formats.default)) return null;
    for (const field of [audio.speed, audio.pitch, audio.volume]) {
        if (!field || typeof field.supported !== "boolean" || ![field.min, field.max, field.step, field.default].every(Number.isFinite) || field.min > field.max || field.step <= 0 || field.default < field.min || field.default > field.max) return null;
    }
    if (typeof audio.instructions?.supported !== "boolean" || !Number.isInteger(audio.references?.promptMaxChars) || audio.references.promptMaxChars < 0) return null;
    return structuredClone(audio);
}

export function normalizeAudioNumericValue(value: string, capability: AudioNumericCapability) {
    return value?.trim() ? value : String(capability.default);
}
