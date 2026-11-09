import { createRoot } from "react-dom/client";
import { useState, type ComponentProps } from "react";
import { App, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CanvasProjectNodePrompt } from "@/pages/canvas/canvas-project-node-prompt";
import { getAntThemeConfig } from "@/lib/app-theme";
import { buildCanvasNodeMentionReferenceMap } from "@/lib/canvas/canvas-resource-references";
import { writeCanvasNodePrompt } from "@/lib/canvas/canvas-node-prompt";
import { applySkinTheme, DEFAULT_CLASSIC_SKIN } from "@/lib/skin-themes";
import { useThemeStore } from "@/stores/use-theme-store";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";

const query = new URLSearchParams(location.search);
const dark = query.get("theme") === "dark";
useThemeStore.getState().setTheme(dark ? "dark" : "light");
applySkinTheme(DEFAULT_CLASSIC_SKIN, dark ? "dark" : "light");
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const withReferences = query.has("references");
const initial: CanvasNodeData = { id: "quick-target", type: CanvasNodeType.Image, title: "固定目标图片", width: 500, height: 300, position: { x: 0, y: 0 }, metadata: { prompt: withReferences ? "  第一行。\n左边右边，仍引用 @[node:reference-a]。  " : "保持这段提示词和目标节点。" } };
const preview = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jO1sAAAAASUVORK5CYII=";
const images = withReferences ? ["reference-a", "reference-b", "unconnected"].map(id => ({ ...initial, id, title: "同名参考图", metadata: { content: preview } })) : [];
const connections = images.slice(0, 2).map(({ id }) => ({ id: `edge-${id}`, fromNodeId: id, toNodeId: initial.id }));
type PromptProps = ComponentProps<typeof CanvasProjectNodePrompt>;
const noOp = () => {};

function Harness() {
    const [node, setNode] = useState(initial);
    const [active, setActive] = useState(false);
    const [toggleCount, setToggleCount] = useState(0);
    const [pointerCount, setPointerCount] = useState(0);
    const [generationCount, setGenerationCount] = useState(0);
    const nodes = [node, ...images];
    const props = {
        node, projectId: "quick-ui-test", videoModel: "", onFocusNode: noOp, onCreateReplacement: noOp, onNodeMouseDown: noOp,
        document: { connections, nodesRef: { current: nodes }, connectionsRef: { current: connections } } as unknown as PromptProps["document"],
        dialogs: { setGenerationHistoryOpen: noOp } as unknown as PromptProps["dialogs"],
        renderModel: { configInputsById: new Map(), mentionReferencesByNodeId: buildCanvasNodeMentionReferenceMap(nodes, connections), semanticNodes: nodes, skillMentionReferences: [] } as unknown as PromptProps["renderModel"],
        generation: { handleGenerateNode: () => setGenerationCount(count => count + 1), runningNodeId: null } as unknown as PromptProps["generation"],
        nodeEditor: { handleConfigNodeChange: noOp, handleNodePromptChange: (_id: string, prompt: string) => setNode(current => writeCanvasNodePrompt(current, prompt)) } as unknown as PromptProps["nodeEditor"],
        pageUi: { getState: () => ({ setDialogNodeId: noOp, setNodeImageSettingsOpen: noOp, setToolbarNodeId: noOp }) } as unknown as PromptProps["pageUi"],
        referenceActions: { handleRemoveNodeReference: noOp, handleReorderNodeReferences: noOp, handleReplaceNodeReference: noOp, handleReplaceNodeReferenceFiles: noOp } as unknown as PromptProps["referenceActions"],
        quickConnectActive: active,
        onToggleQuickConnect: query.has("noToggle") ? undefined : () => { setActive(value => !value); setToggleCount(count => count + 1); },
    } satisfies PromptProps;
    return <ConfigProvider theme={getAntThemeConfig(dark, DEFAULT_CLASSIC_SKIN)}><App><QueryClientProvider client={client}>
        <main style={{ padding: 12, maxWidth: 680, margin: "auto" }}>
            <section data-testid="node-panel" onPointerDown={() => setPointerCount(count => count + 1)}>
                <CanvasProjectNodePrompt {...props} />
            </section>
            <output hidden data-testid="quick-state">{JSON.stringify({ active, toggleCount, pointerCount, generationCount, prompt: node.metadata?.prompt, nodeId: node.id })}</output>
        </main>
    </QueryClientProvider></App></ConfigProvider>;
}

createRoot(document.getElementById("root")!).render(<Harness />);
