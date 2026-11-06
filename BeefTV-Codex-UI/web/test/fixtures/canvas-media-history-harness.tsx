import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { CanvasMediaVersionHistory } from "../../src/components/canvas/canvas-media-version-history";
import { CanvasNodeActionContext } from "../../src/components/canvas/canvas-node-action-context";
import { selectCanvasMediaVersion } from "../../src/lib/canvas/canvas-media-version-selection";
import { prepareInPlaceMediaVersion } from "../../src/lib/canvas/canvas-media-versions";
import { reconcileCanvasImageVersionBatches } from "../../src/lib/canvas/canvas-image-version-batch";
import { canvasThemes } from "../../src/lib/canvas-theme";
import { CanvasNodeType, type CanvasNodeData } from "../../src/types/canvas";

const picture = (color: string) => `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="${color}"/></svg>`)}`;
function initial() {
    const node: CanvasNodeData = { id: "result", type: CanvasNodeType.Image, title: "末日断层：双神交锋", width: 640, height: 360, position: { x: 0, y: 0 }, metadata: { content: picture("#35445c"), status: "success" } };
    if (new URLSearchParams(location.search).has("batch")) {
        const slots: CanvasNodeData[] = ["#35445c", "#446c56", "#745a86", "#6e483f"].map((color, index) => ({
            ...node, id: `version-${index}`, metadata: { status: "success", content: picture(color), taskId: `task-${index}`, mediaVersionOwnerId: node.id, batchRootId: node.id },
        }));
        return reconcileCanvasImageVersionBatches([{ ...node, metadata: { imageBatchAsVersions: true, isBatchRoot: true, count: 4, batchChildIds: slots.map(slot => slot.id) } }, ...slots]);
    }
    return prepareInPlaceMediaVersion([node], node.id).map(node => node.id === "result" ? { ...node, metadata: { ...node.metadata, content: picture("#6e483f") } } : node);
}

function Harness() {
    const storage = new URLSearchParams(location.search).has("batch") ? "history-batch-fixture" : "history-fixture";
    const [nodes, setNodes] = useState<CanvasNodeData[]>(() => JSON.parse(localStorage.getItem(storage) || "null") || initial());
    const [dark, setDark] = useState(true);
    const [busy, setBusy] = useState(false);
    useEffect(() => { localStorage.setItem(storage, JSON.stringify(nodes)); }, [nodes, storage]);
    const owner = nodes.find(node => node.id === "result")!;
    const theme = dark ? canvasThemes.dark : canvasThemes.light;
    const shown = { ...owner, metadata: { ...owner.metadata, status: busy ? "loading" as const : "success" as const } };
    return <div style={{ minHeight: "100vh", padding: 64, background: dark ? "#111" : "#f3f3f3", color: theme.node.text }}>
        <div style={{ display: "flex", gap: 16, marginBottom: 48 }}><button onClick={() => setDark(value => !value)}>切换主题</button><button onClick={() => setBusy(value => !value)}>切换生成状态</button></div>
        <CanvasNodeActionContext.Provider value={{ useMediaVersion: id => setNodes(current => selectCanvasMediaVersion(current, id)) }}>
            <div data-fixture-owner style={{ position: "relative", width: 640, height: 360, borderRadius: 12 }}>
                <img data-current-result src={owner.metadata?.content} alt="当前画面" style={{ width: "100%", height: "100%", borderRadius: 12 }} />
                <CanvasMediaVersionHistory node={shown} versions={nodes.filter(node => node.metadata?.mediaVersionOwnerId === owner.id)} theme={theme} readOnly={false} />
            </div>
        </CanvasNodeActionContext.Provider>
        <output data-current-version>{owner.metadata?.versionLabel}</output>
    </div>;
}
createRoot(document.getElementById("root")!).render(<Harness />);
