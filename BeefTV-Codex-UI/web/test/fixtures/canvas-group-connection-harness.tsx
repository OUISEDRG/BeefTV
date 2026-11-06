import { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { App } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CanvasFrameNode } from "../../src/components/canvas/canvas-frame-node";
import { CanvasNode } from "../../src/components/canvas/canvas-node";
import { CanvasConnectionCreateMenu } from "../../src/components/canvas/canvas-workspace-overlays";
import { CanvasConnectionFeedbackNotice, type CanvasConnectionFeedback } from "../../src/components/canvas/canvas-connection-feedback";
import { canvasThemes } from "../../src/lib/canvas-theme";
import { useCanvasConnectionController } from "../../src/pages/canvas/use-canvas-connection-controller";
import { createCanvasPageUiStore } from "../../src/stores/canvas/use-canvas-page-ui-store";
import { defaultConfig } from "../../src/stores/use-config-store";
import { isFrameNode, isNodeHiddenByCollapsedFrame } from "../../src/lib/canvas/canvas-frame";
import { CanvasNodeType, type CanvasConnection, type CanvasNodeData } from "../../src/types/canvas";

const noop = () => {};
const params = new URL(location.href).searchParams;
const scale = Number(params.get("scale") || 1);
const collapsed = params.has("collapsed");
const nested = params.has("nested");
const kind = params.get("kind") as "storyboard" | "reference_set" | undefined;
const initial: CanvasNodeData[] = [
    { id: "group", type: CanvasNodeType.Frame, title: "三个素材组", position: { x: 80, y: 80 }, width: 780, height: 500, metadata: { frame: { collapsed }, workflowKind: kind } },
    ...(nested ? [{ id: "nested", type: CanvasNodeType.Frame, title: "嵌套组", position: { x: 540, y: 140 }, width: 280, height: 320, parentId: "group", metadata: { frame: { collapsed: true } } } as CanvasNodeData] : []),
    ...[0, 1, 2].map((index): CanvasNodeData => ({ id: `asset-${index}`, parentId: nested && index === 2 ? "nested" : "group", type: CanvasNodeType.Image, title: `素材 ${index + 1}`, position: { x: 120 + index * 230, y: 210 }, width: 180, height: 120,
        metadata: params.has("empty") ? {} : { status: "success", content: `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="180" height="120"><rect width="180" height="120" fill="steelblue"/></svg>')}`, naturalWidth: 180, naturalHeight: 120 } })),
    { id: "target", type: params.has("invalid") ? CanvasNodeType.Audio : CanvasNodeType.Video, title: "目标", position: { x: 1100, y: 140 }, width: 420, height: 236 },
];

function Harness() {
    const [pageUi] = useState(createCanvasPageUiStore);
    const [nodes, setNodes] = useState(initial);
    const [connections, setConnections] = useState<CanvasConnection[]>([]);
    const [interactions, setInteractions] = useState(0);
    const [moved, setMoved] = useState<string | null>(null);
    const [feedback, setFeedback] = useState<CanvasConnectionFeedback | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const nodesRef = useRef(nodes); nodesRef.current = nodes;
    const connectionsRef = useRef(connections); connectionsRef.current = connections;
    const viewportRef = useRef({ x: 0, y: 0, k: scale });
    const controller = useCanvasConnectionController({ pageUi, projectId: "group-fixture", config: defaultConfig, defaultDrawingEngine: "excalidraw", nodesRef, nodeStackOrder: [], connectionsRef, viewportRef,
        scriptScrollTopById: {}, screenToCanvas: (x, y) => ({ x: x / scale, y: y / scale }), setNodes, setConnections, setDrawingNodeId: noop, onConnectionInteractionStart: () => setInteractions(value => value + 1), onConnectionFeedback: setFeedback });
    return <div ref={containerRef} data-canvas-viewport style={{ position: "absolute", inset: 0 }}>
        <div style={{ position: "absolute", transform: `scale(${scale})`, transformOrigin: "0 0" }}>
            {nodes.filter(node => !isNodeHiddenByCollapsedFrame(node, nodes)).map(node => isFrameNode(node)
                ? <CanvasFrameNode key={node.id} data={node} childNodes={nodes.filter(item => item.parentId === node.id)} scale={scale} isSelected isDropTarget={false} isConnectionTarget={controller.connectionTargetNodeId === node.id}
                    onMouseDown={(_event, id) => setMoved(id)} onResize={noop} onToggleCollapsed={noop} onFolderStyleChange={noop} onTitleChange={noop} onContextMenu={noop} onConnectStart={controller.handleConnectStart} />
                : <CanvasNode key={node.id} data={node} scale={scale} isSelected isRelated={false} isFocusRelated={false} isConnectionTarget={controller.connectionTargetNodeId === node.id || controller.batchConnectionPreview?.targetNodeId === node.id} showImageInfo={false}
                    onMouseDown={(_event, id) => setMoved(id)} onHoverStart={noop} onHoverEnd={noop} onConnectStart={controller.handleConnectStart} onResize={noop} onContentChange={noop} onContextMenu={noop} />)}
        </div>
        {controller.pendingConnectionCreate ? <CanvasConnectionCreateMenu pending={controller.pendingConnectionCreate} viewport={viewportRef.current} viewportSize={{ width: 1700, height: 900 }} containerRef={containerRef} canCreateDrawing={false}
            getDisabledReason={type => controller.getConnectionCreateDisabledReason(type, controller.pendingConnectionCreate!)} onCreate={type => controller.createConnectedNode(type, controller.pendingConnectionCreate!)} onClose={controller.cancelPendingConnectionCreate} /> : null}
        <button id="cancel" style={{ position: "absolute", left: 1550, top: 820 }} onClick={controller.cancelPendingConnectionCreate}>取消连线</button>
        <CanvasConnectionFeedbackNotice feedback={feedback} onDismiss={() => setFeedback(null)} theme={canvasThemes.dark} />
        <output id="state" hidden>{JSON.stringify({ connections, nodeIds: nodes.map(node => node.id), pending: controller.pendingConnectionCreate, target: controller.batchConnectionPreview?.targetNodeId || controller.connectionTargetNodeId, batch: controller.batchConnectionPreview, interactions, moved })}</output>
    </div>;
}

createRoot(document.getElementById("root")!).render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><App><Harness /></App></QueryClientProvider>);
