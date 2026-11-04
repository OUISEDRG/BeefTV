import { modelCapabilityConfigFor } from "./model-capabilities";
import type { AiConfig } from "@/stores/use-config-store";

export function generationPromptRequired(config: AiConfig, mode: string): boolean {
    return mode !== "video" || modelCapabilityConfigFor(config, config.model || config.videoModel).video?.promptRequired !== false;
}
