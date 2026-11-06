import { useCallback, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { App, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CanvasNode } from "@/components/canvas/canvas-node";
import { CanvasFrameNode } from "@/components/canvas/canvas-frame-node";
import { InfiniteCanvas } from "@/components/canvas/infinite-canvas";
import { CanvasQuickConnectControl } from "@/components/canvas/canvas-quick-connect-control";
import { CanvasConnectionFeedbackNotice, type CanvasConnectionFeedback } from "@/components/canvas/canvas-connection-feedback";
import { useCanvasQuickConnect } from "@/pages/canvas/use-canvas-quick-connect";
import { useCanvasKeyboard } from "@/pages/canvas/use-canvas-keyboard";
import { buildNodeMentionReferences } from "@/lib/canvas/canvas-resource-references";
import { canvasThemes } from "@/lib/canvas-theme";
import { createModelChannel, defaultConfig, encodeChannelModel } from "@/stores/use-config-store";
import { defaultModelCapabilityConfig } from "@/lib/model-capabilities";
import { createCanvasPageUiStore } from "@/stores/canvas/use-canvas-page-ui-store";
import { CanvasNodeType, type CanvasConnection, type CanvasNodeData } from "@/types/canvas";

const noop = () => {};
const nope = () => false;
const picture = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"><rect width="320" height="180" fill="#3a728b"/></svg>');
const parameters = new URLSearchParams(location.search);
const videoFixture = parameters.has("videosSupported");
const audioFixture = parameters.has("audioSupported") || parameters.has("audioDisabledMode");
const audioChannel = createModelChannel({ id: "dreama-audio-reference", enabled: true, interfaceType: "dreama-videos-async", models: ["video"], modelProfiles: [{ model: "video", protocol: "dreama-videos-async", capability: "video", videoCapabilitiesVersion: "ordinary-audio-v1", capabilityConfig: { version: 1, video: {
    ...defaultModelCapabilityConfig("dreama-videos-async").video!,
    operations: ["text_to_video", "image_to_video", "reference_to_video", "audio_to_video"],
    references: { minImages: 0, maxImages: 30, maxVideos: 10, maxAudios: 10 },
    operationReferences: { image_to_video: { maxImages: 30, maxAudios: 10 }, reference_to_video: { maxImages: 30, maxVideos: 10, maxAudios: 10 }, audio_to_video: { maxImages: 0, maxAudios: parameters.has("audioDisabledMode") ? 0 : 10 } },
} } }] });
const audioModel = encodeChannelModel(audioChannel.id, "video");
const config = audioFixture || videoFixture ? { ...defaultConfig, channels: [audioChannel], model: audioModel, videoModel: audioModel } : defaultConfig;
const make = (id: string, type: CanvasNodeType, x: number, y: number, metadata?: CanvasNodeData["metadata"], parentId?: string): CanvasNodeData => ({ id, type, title: id, position: { x, y }, width: 180, height: 102, metadata, parentId });
const initial = [make("image-one", CanvasNodeType.Image, 40, 130, { content: picture }), make("image-two", CanvasNodeType.Image, 260, 130, { content: picture }),
    make("video", CanvasNodeType.Video, 480, 130, { content: "/fixture.mp4", videoPreview: { content: picture } }),
    make("ancestor", CanvasNodeType.Image, 700, 130, { content: picture }),
    { ...make("group", CanvasNodeType.Frame, 40, 340, { frame: { collapsed: true, expandedWidth: 500, expandedHeight: 300 } }), width: 240, height: 144 },
    make("inner", CanvasNodeType.Frame, 0, 0, undefined, "group"), make("group-one", CanvasNodeType.Image, 0, 0, { content: picture, shotIndex: 0 }, "group"),
    make("group-two", CanvasNodeType.Image, 0, 100, { content: picture, shotIndex: 1 }, "inner"),
    make("group-empty", CanvasNodeType.Image, 0, 200, undefined, "group"),
    make("group-loading", CanvasNodeType.Video, 0, 300, { content: "/fixture.mp4", status: "loading" }, "group"),
    { ...make("empty-group", CanvasNodeType.Frame, 340, 340, { frame: { collapsed: true, expandedWidth: 400, expandedHeight: 300 } }), width: 240, height: 144 },
    make("target", parameters.has("imageTarget") ? CanvasNodeType.Image : CanvasNodeType.Video, 700, 340, { prompt: "Keep target prompt", ...(audioFixture || videoFixture ? { model: audioModel } : {}) }),
    make("empty-image", CanvasNodeType.Image, 40, 540),
    make("loading-video", CanvasNodeType.Video, 260, 540, { content: "/fixture.mp4", status: "loading" }),
    ...(audioFixture ? [make("audio", CanvasNodeType.Audio, 700, 540, { storageKey: "resource:ordinary-audio", durationMs: 5_000, mimeType: "audio/mpeg" })] : []),
    ...(videoFixture ? [make("video-two", CanvasNodeType.Video, 480, 540, { storageKey: "resource:video-two", videoPreview: { content: picture } }), make("video-three", CanvasNodeType.Video, 700, 540, { storageKey: "resource:video-three", videoPreview: { content: picture } })] : [])];

function Harness() {
    const [nodes, setNodes] = useState(initial);
    const nodesRef = useRef(nodes); nodesRef.current = nodes;
    const [connections, setConnections] = useState<CanvasConnection[]>([{ id: "existing-outgoing", fromNodeId: "target", toNodeId: "ancestor" }]);
    const connectionsRef = useRef(connections); connectionsRef.current = connections;
    const containerRef = useRef<HTMLDivElement>(null);
    const [viewport, setViewport] = useState({ x: 0, y: 0, k: 1 });
    const [projectId, setProjectId] = useState("project-one");
    const [dialogNodeId, setDialogNodeId] = useState<string | null>("target");
    const [selected, setSelected] = useState("target");
    const [readOnly, setReadOnly] = useState(false);
    const [plays, setPlays] = useState(0);
    const [previews, setPreviews] = useState(0);
    const [renames, setRenames] = useState(0);
    const [collapses, setCollapses] = useState(0);
    const [cancelCount, setCancelCount] = useState(0);
    const [lod, setLod] = useState({ handled: false, defaultPrevented: false, normal: 0 });
    const [feedback, setFeedback] = useState<CanvasConnectionFeedback | null>(null);
    const [pageUi] = useState(createCanvasPageUiStore);
    const selectedNodeIdsRef = useRef(new Set([selected])); selectedNodeIdsRef.current = new Set([selected]);
    const consumeQuickEscapeRef = useRef(nope);
    const consumeQuickEscape = useCallback(() => consumeQuickEscapeRef.current(), []);
    const deselectCanvas = useCallback(() => { setSelected(""); setDialogNodeId(null); }, []);
    // The real canvas keyboard capture listener mounts before quick-connect.
    useCanvasKeyboard({ pageUi, projectId, nodesRef, selectedNodeIdsRef, selectedConnectionId: null,
        setShortcutRequestNonce: noop, setInfoNodeId: noop, setCropNodeId: noop, setMaskEditNodeId: noop, setAnnotationNodeId: noop,
        saveCanvasProject: noop, zoomToActualSize: noop, fitCanvasContent: noop, fitCanvasSelection: noop, undoCanvas: noop, redoCanvas: noop,
        cancelSelectionBox: noop, copySelectedNodes: noop, pasteCopiedNodes: nope, restoreCopiedNodesFromText: nope, shouldPreferCopiedNodes: nope,
        pasteSystemClipboard: nope, deleteNodes: noop, deleteConnection: noop, deselectCanvas, zoomCanvasIn: noop, zoomCanvasOut: noop,
        autoArrangeCanvasNodes: noop, focusMode: false, exitFocusMode: noop, toggleFocusMode: noop, onOpenSearch: noop, beginBatchConnection: noop,
        consumeEscape: consumeQuickEscape });
    const quick = useCanvasQuickConnect({ projectId, dialogNodeId, readOnly, config, containerRef, nodes, nodesRef, connectionsRef, setConnections,
        cancelConnectionInteraction: () => setCancelCount(count => count + 1), onFeedback: setFeedback });
    consumeQuickEscapeRef.current = quick.consumeEscape;
    const target = nodes.find(node => node.id === "target");
    const references = target ? buildNodeMentionReferences(target, nodes, connections) : [];
    const select = (_event: unknown, id: string) => { setSelected(id); setDialogNodeId(id); };
    return <>
        <nav style={{ position: "absolute", top: 12, left: 20, display: "flex", gap: 12 }}>
            <button onClick={() => setDialogNodeId(null)}>close panel</button>
            <button onClick={() => setProjectId("project-two")}>switch project</button>
            <button onClick={() => setNodes(current => current.filter(node => node.id !== "target"))}>delete target</button>
            <button onClick={() => setReadOnly(value => !value)}>read only</button>
            <button onClick={() => setDialogNodeId("image-one")}>other panel</button>
        </nav>
        <div style={{ position: "absolute", left: 0, top: 50, right: 0, bottom: 0 }}>
            <InfiniteCanvas containerRef={containerRef} viewport={viewport} onViewportChange={setViewport} boxSelectEnabled>
            <aside data-canvas-node-panel data-node-id="target" style={{ position: "absolute", left: 20, top: 8, width: 850 }}>
                <CanvasQuickConnectControl active={Boolean(quick.activeTargetNodeId)} onToggle={() => quick.toggle("target")} theme={canvasThemes.dark} />
                <p data-testid="panel">{dialogNodeId || "closed"} · {target?.metadata?.prompt} · 引用 {references.length}</p>
            </aside>
            {nodes.filter(node => !node.parentId).map(node => node.type === CanvasNodeType.Frame
                ? <CanvasFrameNode key={node.id} data={node} childNodes={nodes.filter(item => item.parentId === node.id)} scale={1} isSelected={selected === node.id} isDropTarget={false}
                    onMouseDown={select} onResize={noop} onToggleCollapsed={() => setCollapses(count => count + 1)} onFolderStyleChange={noop} onTitleChange={noop} onContextMenu={noop} onConnectStart={noop} />
                : <CanvasNode key={node.id} data={node} scale={1} isSelected={selected === node.id} isRelated={false} isFocusRelated={false} isConnectionTarget={false} showImageInfo={false}
                    onMouseDown={select} onHoverStart={noop} onHoverEnd={noop} onConnectStart={noop} onResize={noop} onContentChange={noop} onContextMenu={noop}
                    onTitleChange={() => setRenames(count => count + 1)} onViewImage={() => setPreviews(count => count + 1)} onMediaPlayRequest={() => setPlays(count => count + 1)} />)}
            <button data-testid="lod-source" style={{ position: "absolute", top: videoFixture ? 660 : 540, left: 520 }} onPointerDown={event => {
                const handled = quick.handleSourcePointerDown(event, "image-two");
                const defaultPrevented = event.defaultPrevented;
                setLod(current => ({ handled, defaultPrevented, normal: current.normal + (handled ? 0 : 1) }));
            }}>LOD image source</button>
            </InfiniteCanvas>
        </div>
        <div data-testid="feedback"><CanvasConnectionFeedbackNotice feedback={feedback} onDismiss={() => setFeedback(null)} theme={canvasThemes.dark} /></div>
        <output hidden data-testid="state">{JSON.stringify({ active: quick.activeTargetNodeId, projectId, dialogNodeId, selected, readOnly, plays, previews, renames, collapses, cancelCount, lod, viewport, references: references.map(ref => ref.nodeId), connections })}</output>
    </>;
}

createRoot(document.getElementById("root")!).render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ConfigProvider><App><Harness /></App></ConfigProvider></QueryClientProvider>);
