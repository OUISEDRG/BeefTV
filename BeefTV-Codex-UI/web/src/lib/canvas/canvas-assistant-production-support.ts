import type { AssistantGenerationProposal } from "@/services/api/agent-assistant";
import type { AiConfig } from "@/stores/use-config-store";
import { canvasWorkflowModelAvailable } from "./canvas-provider-capabilities";

export function assistantProductionUnavailableReason(config: AiConfig, proposal: AssistantGenerationProposal): string {
    return proposal.production?.targets.some(target => !canvasWorkflowModelAvailable(config, target.modelKey))
        ? "剧梦暂不支持助手直接制作整片，请从现有画布节点确认项目和剧集后生成。"
        : "";
}
