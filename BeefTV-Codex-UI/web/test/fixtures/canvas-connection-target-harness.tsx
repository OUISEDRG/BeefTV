import React, { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { App } from "antd";
import { useCanvasConnectionController } from "../../src/pages/canvas/use-canvas-connection-controller";
import { createCanvasPageUiStore } from "../../src/stores/canvas/use-canvas-page-ui-store";
import { defaultConfig } from "../../src/stores/use-config-store";
import { CanvasNodeType, type CanvasConnection, type CanvasNodeData } from "../../src/types/canvas";

export function mountConnectionTargetHarness(element: HTMLElement) {
    const scale = Number(new URL(location.href).searchParams.get("scale") || 1);
    const invalid = new URL(location.href).searchParams.has("invalid");
    const batch = new URL(location.href).searchParams.has("batch");
    function Harness() {
        const [pageUi] = useState(createCanvasPageUiStore);
        const [nodes, setNodes] = useState<CanvasNodeData[]>([
            { id: "source", type: CanvasNodeType.Video, title: "source", position: { x: 60, y: 80 }, width: 120, height: 80 },
            { id: "target", type: invalid ? CanvasNodeType.Image : CanvasNodeType.Video, title: "target", position: { x: 400, y: 80 }, width: 720, height: 405 },
        ]);
        const [connections, setConnections] = useState<CanvasConnection[]>([]);
        const [replacement, setReplacement] = useState<string | null>(null);
        const nodesRef = useRef(nodes); nodesRef.current = nodes;
        const connectionsRef = useRef(connections); connectionsRef.current = connections;
        const viewportRef = useRef({ x: 0, y: 0, k: scale });
        const controller = useCanvasConnectionController({ pageUi, projectId: "connection-test", config: defaultConfig, defaultDrawingEngine: "excalidraw", nodesRef, nodeStackOrder: [], connectionsRef, viewportRef, scriptScrollTopById: {}, screenToCanvas: (x, y) => ({ x: x / scale, y: y / scale }), setNodes, setConnections, setDrawingNodeId() {}, onReplaceReference(_target, previous) { setReplacement(previous.id); } });
        return <>
            <div data-canvas-viewport style={{ position: "absolute", inset: 0 }}>
                {nodes.map(node => <div key={node.id} data-node-id={node.id} data-highlighted={controller.connectionTargetNodeId === node.id} style={{ position: "absolute", left: node.position.x * scale, top: node.position.y * scale, width: node.width * scale, height: node.height * scale, background: "#ccc" }}>
                    {node.id === "source" ? <button id="source-pin" style={{ position: "absolute", right: 0, top: "40%" }} onPointerDown={event => batch ? controller.startBatchConnection(event, [node.id]) : controller.handleConnectStart(event, node.id, "source")}>+</button> : "Video"}
                </div>)}
                <div id="overlay" className="ant-popover" style={{ position: "absolute", left: 700 * scale, top: 350 * scale, width: 80, height: 40, background: "white" }}>Overlay</div>
                <div data-canvas-node-panel className="canvas-node-composer-references" style={{ position: "absolute", left: 1200, top: 80, width: 280, height: 100, background: "white" }}>
                    {[0, 1].map(index => <div key={index} data-reference-chip data-target-node-id="target" data-reference-node-id={`old-${index}`} data-reference-id={`reference-${index}`} style={{ position: "absolute", left: 10 + index * 150, top: 10, width: 70, height: 60 }}>Reference {index}</div>)}
                </div>
            </div>
            <output id="state" style={{ position: "absolute", top: 800 }}>{JSON.stringify({ connections, replacement, pending: Boolean(controller.pendingConnectionCreate), target: batch ? controller.batchConnectionPreview?.targetNodeId : controller.connectionTargetNodeId })}</output>
        </>;
    }
    createRoot(element).render(<App><Harness /></App>);
}
