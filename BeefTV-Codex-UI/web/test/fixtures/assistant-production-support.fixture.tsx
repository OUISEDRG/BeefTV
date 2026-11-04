import { expect, mock } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { createModelChannel, defaultConfig, useConfigStore } from "../../src/stores/use-config-store";
import type { AssistantGenerationProposal, AssistantTurn } from "../../src/services/api/agent-assistant";
import { CanvasNodeType, type CanvasNodeData } from "../../src/types/canvas";

const dreama = createModelChannel({ id: "dreama", name: "Dreama", enabled: true, apiKey: "synthetic", models: ["image", "audio"], modelProfiles: [{ model: "image", capability: "image", protocol: "dreama-images-async" }, { model: "audio", capability: "audio", protocol: "dreama-audio-async" }] });
const other = createModelChannel({ id: "other", name: "Other", enabled: true, apiKey: "synthetic", models: ["image"], modelProfiles: [{ model: "image", capability: "image", protocol: "openai-image" }] });
const config = { ...defaultConfig, channels: [dreama, other], model: "dreama::image", imageModel: "dreama::image" };
useConfigStore.setState({ config });
const node: CanvasNodeData = { id: "image", type: CanvasNodeType.Image, title: "Image", position: { x: 0, y: 0 }, width: 300, height: 300, metadata: { model: "dreama::image", composerContent: "visible prompt" } };
const base: AssistantGenerationProposal = { proposalId: "production", kind: "image", nodeIds: [node.id], model: "image", modelKey: "dreama::image", source: { canvasId: "canvas", canvasRevision: 1, modelConfigRevision: 1 } };
const production: AssistantGenerationProposal = { ...base, production: { targets: [{ nodeId: "image", title: "Image", kind: "image", modelKey: "other::image", prompt: "image", parameters: {}, referenceNodeIds: [] }, { nodeId: "audio", title: "Audio", kind: "audio", modelKey: "dreama::audio", prompt: "audio", parameters: {}, referenceNodeIds: [] }] } };
const ordinary = { ...base, proposalId: "ordinary" };
const supported: AssistantGenerationProposal = { ...base, proposalId: "supported", modelKey: "other::image", production: { targets: [production.production!.targets[0]] } };
const api = await import("../../src/services/api/agent-assistant");
const actualConfig = await import("../../src/stores/use-config-store");
const snapshot = await import("../../src/pages/canvas/canvas-assistant-proposal-snapshot");
let prepares = 0, confirms = 0, generations = 0, handled = 0;
mock.module("@/services/api/agent-assistant", () => ({ ...api, confirmAssistantProduction: async () => { confirms++; return { taskIds: ["task"], continuationId: "continuation" }; }, confirmAssistantProposalRunnable: async () => null }));
// SSR uses Zustand's initial server snapshot; supply the same live configuration read by the handler.
mock.module("@/stores/use-config-store", () => ({ ...actualConfig, useEffectiveConfig: () => config }));
mock.module("@/pages/canvas/canvas-assistant-proposal-snapshot", () => ({ ...snapshot, prepareAssistantProposalSnapshot: async () => { prepares++; return { nodes: [node], connections: [], config, skills: [], assets: [] }; } }));
const { CanvasAssistantTurnView } = await import("../../src/pages/canvas/canvas-assistant-turn");
const { useCanvasAssistantProposal } = await import("../../src/pages/canvas/use-canvas-assistant-proposal");
const turn = (proposal: AssistantGenerationProposal): AssistantTurn => ({ turnId: "turn", userText: "create", selectedNodeIds: [], reply: "", toolCalls: [], change: null, proposals: [proposal], error: null, cancelled: false, createdAt: "2026-11-01" });
const render = (proposal: AssistantGenerationProposal) => renderToStaticMarkup(<CanvasAssistantTurnView turn={turn(proposal)} handledProposals={new Set()} onLocate={() => {}} onUndo={() => {}} onRunProposal={() => {}} onDismissProposal={() => {}} />);
expect(render(production)).not.toContain("确认并制作整片");
expect(render(production)).toContain("从现有画布节点确认项目和剧集后生成");
expect(render(supported)).toContain("确认并制作整片");
expect(render(ordinary)).toContain(">生成<");
expect(render(ordinary)).not.toContain("暂不支持助手直接制作整片");

let controller: ReturnType<typeof useCanvasAssistantProposal>;
function Host() {
    controller = useCanvasAssistantProposal({ projectId: "canvas", addedSkills: [], nodesRef: { current: [node] }, connectionsRef: { current: [] }, handledProposals: new Set(), markProposalHandled: () => { handled++; }, handleGenerateNode: async () => { generations++; } });
    return null;
}
renderToStaticMarkup(<Host />);
controller!.runAssistantProposal(production, "turn");
await new Promise(resolve => setTimeout(resolve, 0));
expect({ prepares, confirms, generations, handled }).toEqual({ prepares: 0, confirms: 0, generations: 0, handled: 0 });
console.log("PASS: Dreama production confirmation is absent and direct callbacks cannot submit");
controller!.runAssistantProposal(ordinary, "turn");
await new Promise(resolve => setTimeout(resolve, 0));
expect({ prepares, confirms, generations }).toEqual({ prepares: 1, confirms: 0, generations: 1 });
controller!.runAssistantProposal(supported, "turn");
await new Promise(resolve => setTimeout(resolve, 0));
expect({ prepares, confirms, generations, handled }).toEqual({ prepares: 2, confirms: 1, generations: 1, handled: 1 });
console.log("PASS: ordinary Dreama proposals and other supported production proposals retain their paths");
