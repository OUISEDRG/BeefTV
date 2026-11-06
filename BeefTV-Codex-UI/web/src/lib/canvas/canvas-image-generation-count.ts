import type { ImageCapabilityConfig } from "@/lib/model-capabilities";

export const CANVAS_IMAGE_GENERATION_MAX_COUNT = 8;

type ImageOutputProfile = Pick<ImageCapabilityConfig, "fixedOutputs" | "generationOutputs">;

// Canvas totals describe repeated submissions; the provider's per-request output
// count remains unchanged. Native multi-output models retain their batch choices.
export function canvasImageGenerationCountOptions(profile: ImageOutputProfile, maxCount = 15): number[] {
    if (profile.fixedOutputs && profile.fixedOutputs > 1) {
        const counts = profile.generationOutputs?.filter(value => value <= maxCount);
        return counts?.length ? counts : [profile.fixedOutputs];
    }
    return Array.from({ length: Math.max(1, Math.min(CANVAS_IMAGE_GENERATION_MAX_COUNT, Math.floor(maxCount))) }, (_, index) => index + 1);
}

export function resolveCanvasImageGenerationCount(profile: ImageOutputProfile, value: unknown, maxCount = 15): number {
    const options = canvasImageGenerationCountOptions(profile, maxCount);
    const parsed = Number(value);
    const requested = Number.isFinite(parsed) ? Math.floor(parsed) : options[0];
    if (profile.fixedOutputs && profile.fixedOutputs > 1) return options.includes(requested) ? requested : options[0];
    return Math.max(options[0], Math.min(options[options.length - 1], requested));
}
