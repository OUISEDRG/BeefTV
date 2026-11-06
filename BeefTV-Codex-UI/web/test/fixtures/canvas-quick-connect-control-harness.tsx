import { createRoot } from "react-dom/client";
import { useState, type ComponentProps } from "react";
import { App, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CanvasProjectNodePrompt } from "@/pages/canvas/canvas-project-node-prompt";
import { getAntThemeConfig } from "@/lib/app-theme";
import { applySkinTheme, DEFAULT_CLASSIC_SKIN } from "@/lib/skin-themes";
import { useThemeStore } from "@/stores/use-theme-store";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";

const query = new URLSearchParams(location.search);
const dark = query.get("theme") === "dark";
useThemeStore.getState().setTheme(dark ? "dark" : "light");
applySkinTheme(DEFAULT_CLASSIC_SKIN, dark ? "dark" : "light");
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const initial: CanvasNodeData = { id: "quick-target", type: CanvasNodeType.Image, title: "固定目标图片", width: 500, height: 300, position: { x: 0, y: 0 }, metadata: { prompt: "保持这段提示词和目标节点。" } };
type PromptProps = ComponentProps<typeof CanvasProjectNodePrompt>;
const noOp = () => {};

function Harness() {
    const [node, setNode] = useState(initial);
    const [active, setActive] = useState(false);
    const [toggleCount, setToggleCount] = useState(0);
    const [pointerCount, setPointerCount] = useState(0);
    const [generationCount, setGenerationCount] = useState(0);
    const props = {
        node, projectId: "quick-ui-test", videoModel: "", onFocusNode: noOp, onCreateReplacement: noOp, onNodeMouseDown: noOp,
        document: { connections: [], nodesRef: { current: [node] }, connectionsRef: { current: [] } } as unknown as PromptProps["document"],
        dialogs: { setGenerationHistoryOpen: noOp } as unknown as PromptProps["dialogs"],
        renderModel: { configInputsById: new Map(), mentionReferencesByNodeId: new Map(), semanticNodes: [node], skillMentionReferences: [] } as unknown as PromptProps["renderModel"],
        generation: { handleGenerateNode: () => setGenerationCount(count => count + 1), runningNodeId: null } as unknown as PromptProps["generation"],
        nodeEditor: { handleConfigNodeChange: noOp, handleNodePromptChange: (_id: string, prompt: string) => setNode(current => ({ ...current, metadata: { ...current.metadata, prompt } })) } as unknown as PromptProps["nodeEditor"],
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
