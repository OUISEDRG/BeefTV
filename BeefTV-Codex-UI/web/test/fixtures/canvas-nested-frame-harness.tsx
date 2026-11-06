import { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { useCanvasSelectionController } from "@/pages/canvas/use-canvas-selection-controller";
import { createCanvasPageUiStore } from "@/stores/canvas/use-canvas-page-ui-store";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";

const noop = () => {};
const initial: CanvasNodeData[] = [
    { id: "outer", type: CanvasNodeType.Frame, title: "集分组", position: { x: 20, y: 100 }, width: 600, height: 500 },
    { id: "inner", parentId: "outer", type: CanvasNodeType.Frame, title: "分镜组", position: { x: 80, y: 180 }, width: 400, height: 280 },
    { id: "video", parentId: "inner", type: CanvasNodeType.Video, title: "原视频", position: { x: 130, y: 240 }, width: 120, height: 70 },
    { id: "image", parentId: "inner", type: CanvasNodeType.Image, title: "分镜图", position: { x: 280, y: 240 }, width: 120, height: 70 },
    { id: "outside", type: CanvasNodeType.Image, title: "其他素材", position: { x: 950, y: 700 }, width: 120, height: 70 },
];

function Harness() {
    const [nodes, setNodes] = useState(initial);
    const nodesRef = useRef(nodes); nodesRef.current = nodes;
    const containerRef = useRef<HTMLDivElement>(null);
    const viewportRef = useRef({ x: 0, y: 0, k: 1 });
    const historyPausedRef = useRef(false);
    const [pageUi] = useState(() => {
        const store = createCanvasPageUiStore();
        if (new URLSearchParams(location.search).has("multi")) store.getState().setSelectedNodeIds(new Set(["outer", "video"]));
        return store;
    });
    const selected = pageUi(state => state.selectedNodeIds);
    const selectedNodeIdsRef = useRef(selected); selectedNodeIdsRef.current = selected;
    const controller = useCanvasSelectionController({ pageUi, projectId: "fixture", containerRef, nodesRef, viewportRef, selectedNodeIdsRef, historyPausedRef,
        screenToCanvas: (x, y) => ({ x, y }), setNodes, cancelPendingConnectionCreate: noop, onCanvasSelectionStart: noop, onNodeInteractionStart: noop, onNodeClick: noop, onDeselect: noop });
    return <div ref={containerRef} style={{ position: "absolute", inset: 0, userSelect: "none" }}>
        {nodes.map(node => <div key={node.id} data-node-id={node.id} data-testid={node.id}
            onPointerDown={event => controller.handleNodeMouseDown(event, node.id)}
            style={{ position: "absolute", left: node.position.x, top: node.position.y, width: node.width, height: node.height, border: "1px solid #555", background: node.type === CanvasNodeType.Frame ? "#ddd" : "#aac" }}>{node.title}</div>)}
        <output data-testid="positions">{JSON.stringify(nodes.map(node => ({ id: node.id, position: node.position, parentId: node.parentId })))}</output>
        <output data-testid="preview">{JSON.stringify(controller.dragPreview ? [...controller.dragPreview.nodeIds] : [])}</output>
    </div>;
}
createRoot(document.getElementById("root")!).render(<Harness />);
