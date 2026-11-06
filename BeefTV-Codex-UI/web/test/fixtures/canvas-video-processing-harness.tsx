import { useEffect, useRef, useState, type ComponentProps } from "react";
import { createRoot } from "react-dom/client";
import { App, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CanvasNodeToolbar } from "../../src/components/canvas/canvas-node-toolbar";
import { CanvasNodePromptPanel } from "../../src/components/canvas/canvas-node-prompt-panel";
import { createCanvasVideoProcessingDraft } from "../../src/lib/canvas/canvas-video-processing";
import { defaultModelCapabilityConfig } from "../../src/lib/model-capabilities";
import { createModelChannel, defaultConfig, useConfigStore, type AiConfig } from "../../src/stores/use-config-store";
import { CanvasNodeType, type CanvasNodeData } from "../../src/types/canvas";

const channel = createModelChannel({ id: "processing", name: "处理测试渠道", scope: "system", enabled: true, baseUrl: location.origin, apiKey: "fixture", models: ["enhance-one", "enhance-two", "extend"], modelProfiles: [
    ...["enhance-one", "enhance-two"].map((model, index) => ({ model, displayName: `增强方式 ${index + 1}`, capability: "video" as const, protocol: "dreama-enhancement-async", videoCapabilitiesVersion: "v1", capabilityConfig: { version: 1 as const, video: {
        ...defaultModelCapabilityConfig("dreama-enhancement-async").video!, operations: ["upscale"], defaultOperation: "upscale", promptRequired: false,
        references: { minImages: 0, maxImages: 0, maxVideos: 1, maxAudios: 0 }, durationSupported: false,
        resolutions: [], ratios: [], defaultResolution: "", defaultRatio: "",
        generateAudio: { supported: false, default: false }, watermark: { supported: false, default: false },
    } } })),
    { model: "extend", displayName: "延长方式", capability: "video", protocol: "dreama-extension-async", videoCapabilitiesVersion: "v1", capabilityConfig: { version: 1, video: {
        ...defaultModelCapabilityConfig("dreama-extension-async").video!, operations: ["extend"], defaultOperation: "extend", references: { maxImages: 0, maxVideos: 1, maxAudios: 0 },
    } } },
] });
const config: AiConfig = { ...defaultConfig, channels: [channel], model: "processing::extend", videoModel: "processing::extend" };
useConfigStore.setState({ config });
const source: CanvasNodeData = { id: "source", type: CanvasNodeType.Video, title: "原视频", parentId: "episode", position: { x: 30, y: 170 }, width: 240, height: 135,
    metadata: { content: "/source.mp4", storageKey: "resource:source", model: "processing::extend", status: "success" } };

function Harness() {
    const containerRef = useRef<HTMLDivElement>(null);
    const [ready, setReady] = useState(false);
    const [draft, setDraft] = useState<ReturnType<typeof createCanvasVideoProcessingDraft>>();
    const [submissions, setSubmissions] = useState(0);
    useEffect(() => { setReady(true); }, []);
    const handlers = Object.fromEntries(["onKeep", "onLeave", "onInfo", "onEditText", "onDecreaseFont", "onIncreaseFont", "onToggleDialog", "onAnnotate", "onGenerateImage", "onUpload", "onDownload", "onSaveAsset", "onMaskEdit", "onEmotion", "onPortraitTexture", "onCrop", "onSplit", "onUpscale", "onSuperResolve", "onAngle", "onLighting", "onPanorama", "onViewImage", "onExtractVideoFrames", "onExtractAudioFromVideo", "onTrimVideoSegments", "onDepthCapture", "onSubtitles", "onReversePrompt", "onRetry", "onToggleFreeResize", "onToggleLocked", "onDelete"].map(name => [name, () => {}]));
    const toolbarProps = { ...handlers, node: source, viewport: { x: 0, y: 0, k: 1 }, containerRef, extractingVideoFrames: false, extractingAudio: false, trimmingVideo: false,
        onVideoEditOperation: (_source, operation) => setDraft(createCanvasVideoProcessingDraft(config, source, operation)),
    } as ComponentProps<typeof CanvasNodeToolbar>;
    return <ConfigProvider><App><div ref={containerRef} style={{ position: "relative", width: "100%", height: 330 }}>
        <div data-node-id={source.id} style={{ position: "absolute", left: 30, top: 170, width: 240, height: 135 }}>原视频</div>
        {ready && <CanvasNodeToolbar {...toolbarProps} />}
    </div>
    {draft && <section aria-label="处理节点配置" style={{ position: "relative", maxWidth: 650, margin: "0 auto" }}>
        <CanvasNodePromptPanel projectId="fixture" node={draft.node} isRunning={false}
            mentionReferences={[{ id: "source", nodeId: "source", kind: "video", label: "视频1", title: "原视频", active: true }]}
            referenceNodes={[source, draft.node]} referenceConnections={[draft.connection]}
            onPromptChange={(_id, prompt) => setDraft(current => current && ({ ...current, node: { ...current.node, metadata: { ...current.node.metadata, prompt, composerContent: prompt } } }))}
            onConfigChange={(_id, patch) => setDraft(current => current && ({ ...current, node: { ...current.node, metadata: { ...current.node.metadata, ...patch } } }))}
            onGenerate={() => setSubmissions(count => count + 1)} />
    </section>}
    <output data-testid="draft">{JSON.stringify(draft)}</output><output data-testid="submissions">{submissions}</output>
    </App></ConfigProvider>;
}
createRoot(document.getElementById("root")!).render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Harness /></QueryClientProvider>);
