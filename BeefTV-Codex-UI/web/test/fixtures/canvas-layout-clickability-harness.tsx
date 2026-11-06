import { useEffect, useRef, useState, type CSSProperties } from "react";
import { createRoot } from "react-dom/client";
import { App, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CanvasNode } from "../../src/components/canvas/canvas-node";
import { CanvasNodeActionContext } from "../../src/components/canvas/canvas-node-action-context";
import { applyCanvasLiveViewport } from "../../src/lib/canvas/canvas-live-viewport";
import { layoutCanvasInsertedNodes } from "../../src/lib/canvas/canvas-layout";
import { CanvasNodeType, type CanvasNodeData } from "../../src/types/canvas";

const scale = 0.25;
const noop = () => {};
const sizes = [[1920, 1080], [1080, 1920], [1200, 1200], [3840, 1080], [1080, 1440], [1920, 1080], [1080, 1920], [1200, 1200]];
const colors = ["#487b9b", "#a27658", "#6c8668", "#7c6f9c", "#5e8c8d", "#9b755e", "#717eac", "#998a55"];
const initial = layoutCanvasInsertedNodes(sizes.map(([width, height], index): CanvasNodeData => ({
    id: `asset-${index}`, type: CanvasNodeType.Image, title: `素材${index + 1} · 横竖混排标题可点击`, position: { x: 0, y: 0 }, width: 400, height: 280,
    metadata: { status: "success", naturalWidth: width, naturalHeight: height,
        content: `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="100%" fill="${colors[index]}"/><circle cx="50%" cy="50%" r="150" fill="#ffffff44"/></svg>`)}` },
})), { x: 200, y: 112.5 }, [], { columns: 4, viewportScale: scale });

function Harness() {
    const [nodes, setNodes] = useState(initial);
    const [clicked, setClicked] = useState("");
    const [renamed, setRenamed] = useState("");
    const containerRef = useRef<HTMLDivElement>(null);
    useEffect(() => { applyCanvasLiveViewport(containerRef.current, { x: 60, y: 120, k: scale }, false); }, [nodes]);
    return <ConfigProvider><App>
        <header style={{ position: "absolute", left: 60, top: 20, color: "white", font: "16px sans-serif" }}>合成测试画布 · 25% · 8 张混合比例素材</header>
        <div ref={containerRef} style={{ position: "absolute", inset: 0, "--canvas-committed-scale": 1 } as CSSProperties}>
            <div data-canvas-world-layer style={{ position: "absolute", transformOrigin: "0 0" }}>
                <CanvasNodeActionContext.Provider value={{ updateMediaNode: (id, update) => setNodes(current => current.map(node => node.id === id ? update(node) : node)) }}>
                    {nodes.map(node => <CanvasNode key={node.id} data={node} scale={scale} isSelected isRelated={false} isFocusRelated={false} isConnectionTarget={false} showImageInfo={false}
                        onMouseDown={(_event, id) => setClicked(id)} onHoverStart={noop} onHoverEnd={noop} onConnectStart={noop} onResize={noop} onContentChange={noop} onContextMenu={noop}
                        onTitleChange={(id, title) => { setNodes(current => current.map(item => item.id === id ? { ...item, title } : item)); setRenamed(id); }} />)}
                </CanvasNodeActionContext.Provider>
            </div>
        </div>
        <output data-testid="clicked" hidden>{clicked}</output><output data-testid="renamed" hidden>{renamed}</output>
        <output data-testid="nodes" hidden>{JSON.stringify(nodes)}</output>
    </App></ConfigProvider>;
}

createRoot(document.getElementById("root")!).render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Harness /></QueryClientProvider>);
