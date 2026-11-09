import type { NodeGenerationContext } from "@/components/canvas/canvas-node-generation";
import type { CanvasNodeGenerationMode } from "@/components/canvas/canvas-node-prompt-panel";
import { prepareDreamaAudioIdentity, prepareDreamaVoiceProviderOptions } from "@/lib/canvas/dreama-voice-generation";
import { assertDreamaReferences, buildDreamaGenerationPreview, dreamaCanvasVideoConfig, freezeDreamaBinding, isDreamaProtocol, mergeDreamaProviderOptions } from "@/lib/canvas/dreama-workbench";
import { dreamaImageToolSupport } from "@/lib/canvas/dreama-image-tool-support";
import { assertImageToolGeneration, dreamaImageToolPromptError } from "@/lib/canvas/image-tool-generation";
import { modelRequestOptions, type ModelReferenceLimits } from "@/lib/model-selection";
import type { Skill } from "@/services/api/skills";
import { resolveDreamaVoiceSample } from "@/services/dreama-voice-sample";
import { resolveSkillMentions } from "@/services/skill-runtime";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { resolveModelRequestConfig, type AiConfig } from "@/stores/use-config-store";
import type { CanvasConnection, CanvasNodeData, CanvasNodeMetadata } from "@/types/canvas";

type ProviderOptions = Record<string, Record<string, unknown>>;

// The installed SDK handles declarative provider requests. This local adapter
// handles canvas bindings and authored reference rules before task creation.
export function captureCanvasGenerationProvider(projectId: string, mode: CanvasNodeGenerationMode) {
    const binding = structuredClone(useCanvasStore.getState().openProject(projectId)?.integrations?.dreama);

    return {
        scopeConfig(config: AiConfig, node?: CanvasNodeData): AiConfig {
            if (mode !== "video") return config;
            const scoped = dreamaCanvasVideoConfig(config, binding, node?.metadata?.size);
            if (scoped.error) throw new Error(scoped.error);
            return scoped.config;
        },
        prepareContext<T extends NodeGenerationContext>(context: T): T {
            return prepareDreamaAudioIdentity(context, mode);
        },
        hasBoundVoice(context: NodeGenerationContext): boolean {
            return Boolean(context.dreamaAudioVoiceBinding || context.referenceImages.some(image => image.dreamaVoiceBinding));
        },
        hasVoiceSource(context: NodeGenerationContext): boolean {
            return Boolean(context.dreamaAudioVoiceBinding || context.resolvedCharacterVoices.length || context.referenceAudios.length);
        },
        async prepareProviderContext<T extends NodeGenerationContext>(context: T, config: AiConfig, signal: AbortSignal, mergeOptions?: (options?: ProviderOptions) => ProviderOptions): Promise<T> {
            const voiceOptions = await prepareDreamaVoiceProviderOptions(context, config, mode, id => resolveDreamaVoiceSample(id, signal));
            return { ...context, dreamaVoiceProviderOptions: mergeOptions ? mergeOptions(voiceOptions) : voiceOptions };
        },
        plan(config: AiConfig, nodeId: string, node: CanvasNodeData | undefined, nodes: CanvasNodeData[], connections: CanvasConnection[], prompt: string, skills: Skill[]) {
            if (mode === "text" || !isDreamaProtocol(resolveModelRequestConfig(config, config.model).interfaceType)) return;
            const frozenBinding = freezeDreamaBinding(binding, projectId, nodeId, nodes);
            if (mode === "image" && node) {
                const toolPromptError = dreamaImageToolPromptError(node);
                if (toolPromptError) throw new Error(toolPromptError);
                const toolKind = node.metadata?.emotionEdit ? "emotion" : node.metadata?.imageTool?.kind;
                if (toolKind) {
                    const support = dreamaImageToolSupport(toolKind, node, config, { id: projectId, integrations: { dreama: binding }, nodes }, "generation");
                    if (support?.disabled) throw new Error(support.reason);
                }
                assertImageToolGeneration(node, nodes, connections, prompt);
            }
            if (node?.metadata?.cameraControl?.enabled || resolveSkillMentions(prompt, skills).length) {
                throw new Error("剧梦生成只转换引用标记，不自动添加摄影、肖像或技能正文。请将所需内容写入提示词，并关闭对应设置或移除技能引用后生成。");
            }
            return {
                referenceOnly: true,
                constrainModelConfig(input: AiConfig): AiConfig {
                    return mode === "video" ? { ...input, channels: config.channels, size: config.size } : input;
                },
                validateSkills(compiledPrompt: string) {
                    if (resolveSkillMentions(compiledPrompt, skills).length) {
                        throw new Error("剧梦生成不自动添加技能正文。请将所需内容写入提示词，并移除技能引用后生成。");
                    }
                },
                validateReferences(context: NodeGenerationContext, limits?: ModelReferenceLimits, operation?: string) {
                    assertDreamaReferences(context, limits, operation);
                },
                validateStyle() {
                    throw new Error("剧梦生成不自动添加项目画风正文。请将所需风格写入提示词，并停用对应项目画风后生成。");
                },
                mergeOptions(options?: ProviderOptions) {
                    return mergeDreamaProviderOptions(frozenBinding, options);
                },
                previewMetadata(config: AiConfig, context: NodeGenerationContext): Pick<CanvasNodeMetadata, "dreamaGenerationPreview"> {
                    return { dreamaGenerationPreview: buildDreamaGenerationPreview(frozenBinding, config.model, mode, modelRequestOptions(config, mode), context) };
                },
            };
        },
    };
}
