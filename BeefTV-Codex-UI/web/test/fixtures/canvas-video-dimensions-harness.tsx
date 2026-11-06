import { useCallback, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { CanvasNodeContent } from "../../src/components/canvas/canvas-node-content";
import { CanvasNodeActionContext } from "../../src/components/canvas/canvas-node-action-context";
import { canvasThemes } from "../../src/lib/canvas-theme";
import { CanvasNodeType, type CanvasNodeData } from "../../src/types/canvas";

declare global {
    interface Window {
        videoDimensionsFixture: {
            setNode: (node: CanvasNodeData) => void;
            setActive: (active: boolean) => void;
            node: CanvasNodeData;
            writes: number;
            compatible: boolean;
        };
    }
}

let writes = 0;
function Harness() {
    const [active, setActive] = useState(true);
    const [node, setNode] = useState<CanvasNodeData>({ id: "video", type: CanvasNodeType.Video, title: "视频", position: { x: 100, y: 200 }, width: 720, height: 405,
        metadata: { content: "data:video/mp4;base64,AAAA", status: "success", mimeType: "video/mp4" } });
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const updateMediaNode = useCallback((id: string, update: (current: CanvasNodeData) => CanvasNodeData) => {
        setNode(current => {
            if (current.id !== id) return current;
            const next = update(current);
            if (next !== current) writes++;
            return next;
        });
    }, []);
    window.videoDimensionsFixture = { setNode, setActive, node, writes, compatible: window.videoDimensionsFixture?.compatible || false };
    return <CanvasNodeActionContext.Provider value={{ updateMediaNode }}><div style={{ width: node.width, height: node.height }}>
        <CanvasNodeContent node={node} theme={canvasThemes.light} mediaActive={active} isEditingContent={false} textareaRef={textareaRef}
            isBatchRoot={false} batchCount={0} batchExpanded={false} batchOpening={false} batchRecovering={false}
            onContentChange={() => {}} onStopEditing={() => {}} mentionReferences={[]} />
    </div></CanvasNodeActionContext.Provider>;
}
createRoot(document.getElementById("root")!).render(<Harness />);
