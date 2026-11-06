import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { App, Button, Input, Modal, Tag } from "antd";
import { Camera, Images, Maximize2, Plus, Settings2, SlidersHorizontal, UserRound } from "lucide-react";

import { canvasDockStyle } from "@/lib/canvas/canvas-aceternity-style";
import { ASSET_CATEGORY_OPTIONS } from "@/lib/asset-category";
import { canvasThemes } from "@/lib/canvas-theme";
import { resolveNodeToolbarPlacement, resolveToolbarTools, type NodeToolbarGroup, type ToolContext, type ToolbarHandlers } from "@/lib/canvas/tool-registry";
import { subscribeCanvasGraphicsViewportPreview, subscribeCanvasNodeDragPreview } from "@/lib/canvas/canvas-live-viewport";
import { canvasNodeAssetCategory } from "@/lib/canvas/canvas-node-asset";
import type { ImageSplitParams } from "@/lib/canvas/canvas-image-data";
import { formatBytes, getDataUrlByteSize } from "@/lib/image-utils";
import { GenerationFailureNotice } from "@/components/generation/generation-failure-notice";
import { explainGenerationError } from "@/lib/generation-error";
import { canvasNodeDiagnosticContext } from "@/services/diagnostics/canvas-media-diagnostics";
import { useCopyText } from "@/hooks/use-copy-text";
import { useActiveTheme } from "@/stores/canvas/use-canvas-theme-store";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { useEffectiveConfig } from "@/stores/use-config-store";
import { CanvasNodeType, type CanvasNodeData, type CanvasNodeMetadata, type CanvasWorkspaceMode, type ViewportTransform } from "@/types/canvas";
import { buildImageToolbarTools, resolveImageToolbarLayout } from "./canvas-image-toolbar-tools";
import { NodeDockToolButton, NodeDockMenuButton, compareToolbarTools, type ToolbarTool } from "./canvas-node-dock-tools";
import { DreamaVoiceBindingButton } from "./canvas-image-voice-dialog";
import { parseDreamaVoiceBinding } from "@/lib/dreama-voice";
import { canvasAIUpscaleAvailable, canvasVideoOperationSupport, canvasVideoReversePromptAvailable, type CanvasVideoProcessingOperation } from "@/lib/canvas/canvas-provider-capabilities";
import { canvasVideoProcessingLabels } from "@/lib/canvas/canvas-video-processing";

type CanvasNodeToolbarProps = {
    projectId?: string;
    node: CanvasNodeData | null;
    viewport: ViewportTransform;
    containerRef: RefObject<HTMLDivElement | null>;
    onKeep: (nodeId: string) => void;
    onLeave: () => void;
    onInfo: (node: CanvasNodeData) => void;
    onEditText: (node: CanvasNodeData) => void;
    onDecreaseFont: (node: CanvasNodeData) => void;
    onIncreaseFont: (node: CanvasNodeData) => void;
    onToggleDialog: (node: CanvasNodeData) => void;
    onAnnotate: (node: CanvasNodeData) => void;
    onGenerateImage: (node: CanvasNodeData) => void;
    onUpload: (node: CanvasNodeData) => void;
    onDownload: (node: CanvasNodeData) => void;
    onSaveAsset: (node: CanvasNodeData) => void;
    onBindVoice?: (node: CanvasNodeData) => void;
    onMaskEdit: (node: CanvasNodeData) => void;
    onEmotion: (node: CanvasNodeData) => void;
    onPortraitTexture: (node: CanvasNodeData) => void;
    onCrop: (node: CanvasNodeData) => void;
    onSplit: (node: CanvasNodeData, params: ImageSplitParams) => void;
    onUpscale: (node: CanvasNodeData) => void;
    onSuperResolve: (node: CanvasNodeData) => void;
    onDraftToFinal?: (node: CanvasNodeData, resolution: "720p" | "1080p") => void;
    onVideoEditOperation?: (node: CanvasNodeData, operation: CanvasVideoProcessingOperation) => void;
    onAngle: (node: CanvasNodeData) => void;
    onLighting: (node: CanvasNodeData) => void;
    onPanorama: (node: CanvasNodeData) => void;
    onViewImage: (node: CanvasNodeData) => void;
    onExtractVideoFrames: (node: CanvasNodeData, preset?: "current" | "first" | "last") => void;
    onExtractAudioFromVideo: (node: CanvasNodeData) => void;
    onTrimVideoSegments: (node: CanvasNodeData) => void;
    onDepthCapture: (node: CanvasNodeData) => void;
    onSubtitles: (node: CanvasNodeData) => void;
    extractingVideoFrames: boolean;
    extractingAudio: boolean;
    trimmingVideo: boolean;
    onReversePrompt: (node: CanvasNodeData) => void;
    onRetry: (node: CanvasNodeData) => void;
    onToggleFreeResize: (node: CanvasNodeData) => void;
    onToggleLocked: (node: CanvasNodeData) => void;
    onDelete: (node: CanvasNodeData) => void;
    workspaceMode?: CanvasWorkspaceMode;
};

type CanvasAssetCategory = NonNullable<NonNullable<CanvasNodeData["metadata"]>["assetCategory"]>;

const assetCategoryOptions: Array<{ value: CanvasAssetCategory; label: string }> = ASSET_CATEGORY_OPTIONS;


export function CanvasNodeToolbar({
    projectId,
    node,
    viewport,
    containerRef,
    onKeep,
    onLeave,
    onInfo,
    onEditText,
    onDecreaseFont,
    onIncreaseFont,
    onToggleDialog,
    onAnnotate,
    onGenerateImage,
    onUpload,
    onDownload,
    onSaveAsset,
    onBindVoice,
    onMaskEdit,
    onEmotion,
    onPortraitTexture,
    onCrop,
    onSplit,
    onUpscale,
    onSuperResolve,
    onDraftToFinal,
    onVideoEditOperation,
    onAngle,
    onLighting,
    onPanorama,
    onViewImage,
    onExtractVideoFrames,
    onExtractAudioFromVideo,
    onTrimVideoSegments,
    onDepthCapture,
    onSubtitles,
    extractingVideoFrames,
    extractingAudio,
    trimmingVideo,
    onReversePrompt,
    onRetry,
    onToggleFreeResize,
    onToggleLocked,
    onDelete,
    workspaceMode = "professional",
}: CanvasNodeToolbarProps) {
    const [openMenuId, setOpenMenuId] = useState<string | null>(null);
    const [containerWidth, setContainerWidth] = useState(1000);
    const [anchor, setAnchor] = useState<{ left: number; top: number } | null>(null);
    const toolbarRef = useRef<HTMLDivElement>(null);
    const { message } = App.useApp();
    const copyText = useCopyText();
    const themeName = useActiveTheme();
    const theme = canvasThemes[themeName];
    const config = useEffectiveConfig();
    const canvas = useCanvasStore((state) => state.projects.find((project) => project.id === projectId));
    const simpleMode = workspaceMode === "simple";

    useEffect(() => {
        setOpenMenuId(null);
    }, [node?.id]);

    useLayoutEffect(() => {
        const container = containerRef.current;
        if (!node || !container) {
            setAnchor(null);
            return;
        }
        const element = container.querySelector<HTMLElement>(`[data-node-id="${CSS.escape(node.id)}"]`);
        if (!element) {
            setAnchor(null);
            return;
        }
        const title = element.querySelector<HTMLElement>(".canvas-node-external-header");
        const topbar = container.closest("[data-canvas-editor]")?.querySelector<HTMLElement>(".canvas-topbar");
        let disposed = false;
        let queued = false;
        let containerRect = container.getBoundingClientRect();
        let toolbarWidth = toolbarRef.current?.offsetWidth || 0;
        let toolbarHeight = toolbarRef.current?.offsetHeight || 40;
        const update = () => {
            containerRect = container.getBoundingClientRect();
            const nodeRect = element.getBoundingClientRect();
            const titleRect = title?.getBoundingClientRect() || nodeRect;
            const preferredLeft = nodeRect.left - containerRect.left + nodeRect.width / 2;
            const halfToolbar = toolbarWidth / 2;
            const canClamp = toolbarWidth > 0 && toolbarWidth <= containerRect.width - 20;
            let left = canClamp ? Math.min(Math.max(preferredLeft, halfToolbar + 10), containerRect.width - halfToolbar - 10) : preferredLeft;
            const topInset = topbar ? Math.max(0, topbar.getBoundingClientRect().bottom - containerRect.top) : 0;
            const above = titleRect.top - containerRect.top - 6;
            const top = Math.max(topInset + toolbarHeight + 8, Math.min(above, containerRect.height - 8));
            const panelRoot = container.closest("[data-canvas-editor]") || container;
            const panels = [...Array.from(panelRoot.querySelectorAll<HTMLElement>("[data-canvas-node-panel]")), ...Array.from(document.querySelectorAll<HTMLElement>(".canvas-agent-panel"))]
                .map((panel) => panel.getBoundingClientRect()).filter((rect) => rect.width > 0 && rect.height > 0);
            const overlapsPanel = (rect: DOMRect, candidateLeft: number) => candidateLeft + halfToolbar > rect.left - containerRect.left
                && candidateLeft - halfToolbar < rect.right - containerRect.left
                && top > rect.top - containerRect.top && top - toolbarHeight < rect.bottom - containerRect.top;
            let panelBlocked = false;
            for (const rect of panels) {
                if (!overlapsPanel(rect, left)) continue;
                // Keep the title's vertical anchor even when an editor or the
                // Agent covers the node; only use free space beside the panel.
                const candidates = [rect.left - containerRect.left - halfToolbar - 8, rect.right - containerRect.left + halfToolbar + 8]
                    .filter((candidate) => candidate >= halfToolbar + 10 && candidate <= containerRect.width - halfToolbar - 10
                        && panels.every((panel) => !overlapsPanel(panel, candidate)))
                    .sort((a, b) => Math.abs(a - preferredLeft) - Math.abs(b - preferredLeft));
                if (!candidates.length) { panelBlocked = true; break; }
                left = candidates[0];
            }
            // The toolbar is a sibling of the viewport. Its containing block
            // can start above the canvas when a guide or workbench is visible.
            const parent = toolbarRef.current?.offsetParent as HTMLElement | null;
            const parentRect = parent?.getBoundingClientRect() || containerRect;
            const position = {
                left: left + containerRect.left - parentRect.left - (parent?.clientLeft || 0) + (parent?.scrollLeft || 0),
                top: top + containerRect.top - parentRect.top - (parent?.clientTop || 0) + (parent?.scrollTop || 0),
            };
            if (toolbarRef.current) {
                const titleOverlaps = titleRect.right > containerRect.left + left - halfToolbar
                    && titleRect.left < containerRect.left + left + halfToolbar
                    && titleRect.bottom > containerRect.top + top - toolbarHeight
                    && titleRect.top < containerRect.top + top;
                const workbenchRect = topbar?.querySelector<HTMLElement>(".dreama-workbench-panel")?.getBoundingClientRect();
                const workbenchOverlaps = workbenchRect && workbenchRect.width > 0 && workbenchRect.height > 0
                    && workbenchRect.right > containerRect.left + left - halfToolbar
                    && workbenchRect.left < containerRect.left + left + halfToolbar
                    && workbenchRect.bottom > containerRect.top + top - toolbarHeight
                    && workbenchRect.top < containerRect.top + top;
                const nodeVisible = nodeRect.right > containerRect.left && nodeRect.left < containerRect.right
                    && nodeRect.bottom > containerRect.top + topInset && nodeRect.top < containerRect.bottom;
                // At the top edge, do not replace the node name or top bar with
                // a detached toolbar. It returns as soon as there is room.
                toolbarRef.current.style.visibility = nodeVisible && !panelBlocked && !titleOverlaps && !workbenchOverlaps
                    && top <= above + 0.5 && top <= containerRect.height - 8 ? "" : "hidden";
                toolbarRef.current.style.transform = `translate3d(${position.left}px, ${position.top}px, 0)`;
                return;
            }
            setAnchor((current) => (current?.left === position.left && current.top === position.top ? current : position));
        };
        const scheduleUpdate = () => {
            if (queued || disposed) return;
            queued = true;
            queueMicrotask(() => {
                queued = false;
                if (!disposed) update();
            });
        };
        const measure = () => {
            containerRect = container.getBoundingClientRect();
            toolbarWidth = toolbarRef.current?.offsetWidth || 0;
            toolbarHeight = toolbarRef.current?.offsetHeight || 40;
            setContainerWidth(containerRect.width);
            scheduleUpdate();
        };
        measure();
        const resizeObserver = new ResizeObserver(measure);
        resizeObserver.observe(element);
        if (title) resizeObserver.observe(title);
        if (topbar) resizeObserver.observe(topbar);
        resizeObserver.observe(container);
        if (toolbarRef.current) resizeObserver.observe(toolbarRef.current);
        const topbarObserver = new MutationObserver(scheduleUpdate);
        if (topbar) topbarObserver.observe(topbar, { childList: true, subtree: true });
        const unsubscribeViewport = subscribeCanvasGraphicsViewportPreview(container, scheduleUpdate);
        const unsubscribeDrag = subscribeCanvasNodeDragPreview(container, scheduleUpdate);
        window.addEventListener("resize", measure);
        return () => {
            disposed = true;
            resizeObserver.disconnect();
            topbarObserver.disconnect();
            unsubscribeViewport();
            unsubscribeDrag();
            window.removeEventListener("resize", measure);
        };
    }, [anchor === null, containerRef, node, viewport.k, viewport.x, viewport.y]);

    if (!node || !anchor || node.type === CanvasNodeType.Director || node.metadata?.directorSceneId) return null;

    const isImage = node.type === CanvasNodeType.Image;
    const isVideo = node.type === CanvasNodeType.Video;
    const isAudio = node.type === CanvasNodeType.Audio;
    const hasImage = isImage && Boolean(node.metadata?.content);
    const canDraftToFinal = isVideo && Boolean(node.metadata?.content) && node.metadata?.draft === "true" && Boolean(node.metadata?.taskProviderRequestId?.trim()) && canvasVideoOperationSupport(config, node, "draft_to_final").available;
    const canAIUpscale = canvasAIUpscaleAvailable(config);
    const copyImagePrompt = (target: CanvasNodeData) => {
        const prompt = target.metadata?.prompt?.trim();
        if (!prompt) {
            message.warning("暂无可复制的提示词");
            return;
        }
        copyText(prompt, "提示词已复制");
    };
    const imageTools = buildImageToolbarTools(node, {
        onUpload,
        onToggleFreeResize,
        onAnnotate,
        onMaskEdit,
        onEmotion,
        onPortraitTexture,
        onCrop,
        onUpscale,
        onSuperResolve,
        onAngle,
        onLighting,
        onPanorama,
        onViewImage,
        onCopyPrompt: copyImagePrompt,
        onReversePrompt,
    }, config, canvas);

    // 构建 ToolContext——供注册表解析工具
    const nodeHoverHandlers = {
        onNodeInfo: onInfo,
        onNodeDelete: onDelete,
        onNodeRetry: onRetry,
        onNodeEditText: onEditText,
        onNodeDecreaseFont: onDecreaseFont,
        onNodeIncreaseFont: onIncreaseFont,
        onNodeToggleDialog: onToggleDialog,
        onNodeAnnotate: onAnnotate,
        onNodeGenerateImage: onGenerateImage,
        onNodeUpload: onUpload,
        onNodeDownload: onDownload,
        onNodeSaveAsset: onSaveAsset,
        onNodeMaskEdit: onMaskEdit,
        onNodeEmotion: onEmotion,
        onNodePortraitTexture: onPortraitTexture,
        onNodeCrop: onCrop,
        onNodeSplit: (target) => onSplit(target, { rows: 2, columns: 2 }),
        onNodeUpscale: onUpscale,
        onNodeSuperResolve: onSuperResolve,
        onNodeAngle: onAngle,
        onNodeViewImage: onViewImage,
        onNodeExtractVideoFrames: onExtractVideoFrames,
        onNodeExtractAudioFromVideo: onExtractAudioFromVideo,
        onNodeTrimVideoSegments: onTrimVideoSegments,
        onNodeCropVideo: onCrop,
        onNodeDepthCapture: onDepthCapture,
        onNodeReversePrompt: onReversePrompt,
        onNodeToggleFreeResize: onToggleFreeResize,
        onNodeSubtitles: onSubtitles,
        onNodeToggleLocked: onToggleLocked,
        onNodeCopyPrompt: copyImagePrompt,
    } as Partial<ToolbarHandlers> as ToolbarHandlers;

    const nodeHoverCtx: ToolContext = {
        selectedCount: 0,
        selectedNodeTypes: new Set(),
        selectedVideoCount: 0,
        canvasTool: "move",
        workspaceMode: workspaceMode || "professional",
        isProjectLinked: false,
        canUndo: false,
        canRedo: false,
        node,
        nodeMetadata: node.metadata,
        extractingVideoFrames,
        extractingAudio,
        trimmingVideo,
        mergingVideos: false,
        addPanelOpen: false,
        appearancePanelOpen: false,
        settingsPanelOpen: false,
        handlers: nodeHoverHandlers,
    };

    // 注册表统一提供动作合同、适用性和节点 Dock 层级。
    const registryTools = resolveToolbarTools("node-hover", nodeHoverCtx, null);
    const registryToolbarTools: ToolbarTool[] = registryTools.map((tool) => {
        const placement = resolveNodeToolbarPlacement(tool, nodeHoverCtx);
        return {
            id: tool.id,
            label: tool.displayLabel ? (typeof tool.displayLabel === "function" ? tool.displayLabel(nodeHoverCtx) : tool.displayLabel) : typeof tool.label === "function" ? tool.label(nodeHoverCtx) : tool.label,
            icon: typeof tool.icon === "function" ? tool.icon(nodeHoverCtx) : tool.icon,
            group: placement.group,
            order: placement.order,
            section: tool.nodeToolbar?.section,
            description: tool.nodeToolbar?.description,
            active: tool.active?.(nodeHoverCtx),
            danger: tool.danger,
            disabled: tool.disabled?.(nodeHoverCtx),
            onClick: () => tool.run(nodeHoverCtx),
        };
    });
    const allTools: ToolbarTool[] = hasImage && !simpleMode ? [...registryToolbarTools, ...imageTools] : registryToolbarTools;
    const compact = containerWidth < 640;
    const narrow = containerWidth < 420;
    const inGroup = (group: NodeToolbarGroup) => allTools.filter((tool) => tool.group === group).sort(compareToolbarTools);
    const primary = inGroup("primary");
    const primaryTools = narrow ? primary.slice(0, 1) : primary;
    const imageLayout = resolveImageToolbarLayout(allTools, compact);
    const imagePrimaryTools = imageLayout.primary;
    const portraitTools = isImage ? imageLayout.portrait : compact ? [] : inGroup("portrait");
    const viewpointLightingTools = isImage ? imageLayout.viewpointLighting : compact ? [] : [...inGroup("viewpoint"), ...inGroup("lighting")];
    const panoramaTools = compact || isImage ? [] : inGroup("panorama");
    const processTools = isImage ? imageLayout.process : compact ? [...inGroup("portrait"), ...inGroup("viewpoint"), ...inGroup("lighting"), ...inGroup("panorama"), ...inGroup("process")] : inGroup("process");
    const workspaceTools = narrow ? [] : inGroup("workspace");
    const utilityTools = inGroup("utility");
    const imageSettingsTools = isImage ? allTools.filter((tool) => tool.id === "replace" || tool.id === "resize" || tool.id === "node-lock") : [];
    const processMenuLabel = compact ? "工具" : isVideo ? "提取素材" : isImage ? "图片工具" : isAudio ? "音频处理" : "文本调整";
    const videoAudioTool = registryToolbarTools.find((tool) => tool.id === "extractAudio");
    const videoEditTools: ToolbarTool[] = onVideoEditOperation && (node.metadata?.content || node.metadata?.storageKey) ? (["extend", "upscale", "inpaint"] as const).filter((operation) => canvasVideoOperationSupport(config, node, operation).available).map((operation, index) => ({
        id: `video-${operation}`, label: canvasVideoProcessingLabels[operation], icon: null, group: "process", order: index + 1, onClick: () => onVideoEditOperation(node, operation),
    })) : [];
    const videoProcessingTools: ToolbarTool[] = [
        ...registryToolbarTools.filter((tool) => tool.id === "trimRegenerate" || tool.id === "cropVideo" || tool.id === "depthCapture"),
        ...videoEditTools,
        ...((node.metadata?.content || node.metadata?.storageKey) && canvasVideoReversePromptAvailable(config) ? [{ id: "video-reverse-prompt", label: "反推提示词", icon: null, group: "process" as const, order: 4, onClick: () => onReversePrompt(node) }] : []),
        ...(narrow ? inGroup("workspace") : []),
    ];
    const videoSubtitleTool = registryToolbarTools.find((tool) => tool.id === "subtitles");
    const videoKeyframeTool = registryToolbarTools.find((tool) => tool.id === "extractFrames");
    const videoKeyframeTools: ToolbarTool[] = videoKeyframeTool
        ? [
              { ...videoKeyframeTool, id: "keyframe-current", label: "截取当前帧", onClick: () => onExtractVideoFrames(node, "current") },
              { ...videoKeyframeTool, id: "keyframe-first", label: "截取首帧", onClick: () => onExtractVideoFrames(node, "first") },
              { ...videoKeyframeTool, id: "keyframe-last", label: "截取尾帧", onClick: () => onExtractVideoFrames(node, "last") },
          ]
        : [];
    const videoGlobalTools: ToolbarTool[] = isVideo
        ? [
              {
                  id: "video-preview",
                  label: "预览",
                  icon: <Maximize2 className="size-3.5" />,
                  group: "utility",
                  order: 10,
                  disabled: !Boolean(node.metadata?.content),
                  onClick: () => onViewImage(node),
              },
              ...utilityTools,
          ]
        : [];
    const handleMenuOpenChange = (menuId: string, open: boolean) => {
        setOpenMenuId((current) => (open ? menuId : current === menuId ? null : current));
        if (open) onKeep(node.id);
        else if (!toolbarRef.current?.contains(document.activeElement)) onLeave();
    };
    const dockStyle = canvasDockStyle(theme, theme.node.text);

    return (
        <div
            ref={toolbarRef}
            className="canvas-node-toolbar absolute z-[var(--z-node-toolbar)] -translate-x-1/2 -translate-y-full"
            style={{ left: 0, top: 0, transform: `translate3d(${anchor.left}px, ${anchor.top}px, 0)`, width: "max-content", maxWidth: Math.max(0, containerWidth - 20), color: theme.node.text }}
            onMouseEnter={() => onKeep(node.id)}
            onMouseLeave={() => {
                if (!openMenuId) onLeave();
            }}
            onMouseDown={(event) => event.stopPropagation()}
            onPointerDown={(event) => event.stopPropagation()}
            data-canvas-no-zoom
            onKeyDown={(event) => event.stopPropagation()}
            onFocus={() => onKeep(node.id)}
            onBlur={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget) && !openMenuId) onLeave();
            }}
        >
            <div role="toolbar" aria-label="节点快捷工具" data-canvas-wheel-scroll className="thin-scrollbar flex min-h-10 max-w-full items-center gap-0.5 overflow-x-auto overflow-y-hidden rounded-[var(--dock-radius-tight)] px-1.5 py-1 backdrop-blur-2xl" style={{ ...dockStyle, border: 0 }}>
                {isVideo ? (
                    <>
                        {canDraftToFinal && onDraftToFinal ? (
                            <NodeDockMenuButton
                                menuId="video-draft-to-final"
                                label="转高清成片"
                                icon={<UpscaleToolbarIcon />}
                                tools={[
                                    { id: "draft-to-final-720p", label: "转 720P 成片", icon: null, group: "process", order: 1, onClick: () => onDraftToFinal(node, "720p") },
                                    { id: "draft-to-final-1080p", label: "转 1080P 成片", icon: null, group: "process", order: 2, onClick: () => onDraftToFinal(node, "1080p") },
                                ]}
                                openMenuId={openMenuId}
                                onOpenChange={handleMenuOpenChange}
                                placement="bottomLeft"
                            />
                        ) : null}
                        {canAIUpscale ? <NodeDockToolButton tool={{ id: "ai-upscale", label: "AI超分", icon: <UpscaleToolbarIcon />, group: "process", order: 1, onClick: () => onSuperResolve(node) }} showTooltip={false} /> : null}
                        {videoProcessingTools.length ? <NodeDockMenuButton menuId="video-processing" label="视频处理" icon={null} tools={videoProcessingTools} openMenuId={openMenuId} onOpenChange={handleMenuOpenChange} placement="bottomLeft" /> : null}
                        {videoAudioTool ? <NodeDockToolButton tool={videoAudioTool} showTooltip={false} /> : null}
                        {videoSubtitleTool ? <NodeDockToolButton tool={videoSubtitleTool} showTooltip={false} /> : null}
                        {videoKeyframeTools.length ? (
                            <NodeDockMenuButton menuId="video-keyframes" label="关键帧截取" icon={<Images className="size-3.5" />} tools={videoKeyframeTools} openMenuId={openMenuId} onOpenChange={handleMenuOpenChange} placement="bottomLeft" />
                        ) : null}
                        {workspaceTools.map((tool) => (
                            <NodeDockToolButton key={tool.id} tool={tool} />
                        ))}
                        {videoGlobalTools.length ? <span aria-hidden className="aceternity-dock-separator mx-1 h-5 w-px shrink-0" /> : null}
                        {videoGlobalTools.map((tool) => (
                            <NodeDockToolButton key={tool.id} tool={tool} iconOnly />
                        ))}
                    </>
                ) : (
                    <>
                        {isImage && canAIUpscale ? <NodeDockToolButton tool={{ id: "ai-upscale", label: "AI超分", icon: <UpscaleToolbarIcon />, group: "process", order: 1, onClick: () => onSuperResolve(node) }} showTooltip={false} /> : null}
                        {isImage && onBindVoice && (node.metadata?.content || node.metadata?.storageKey) ? <DreamaVoiceBindingButton binding={parseDreamaVoiceBinding(node.metadata?.dreamaVoiceBinding)} onClick={() => onBindVoice(node)} /> : null}
                        {(isImage ? imagePrimaryTools : primaryTools).map((tool) => (
                            <NodeDockToolButton key={tool.id} tool={tool} showTooltip={tool.id !== "maskEdit"} />
                        ))}
                        {panoramaTools.map((tool) => (
                            <NodeDockToolButton key={tool.id} tool={tool} />
                        ))}
                        {portraitTools.length ? <NodeDockMenuButton menuId="portrait" label="人像调整" icon={<UserRound className="size-3.5" />} tools={portraitTools} openMenuId={openMenuId} onOpenChange={handleMenuOpenChange} /> : null}
                        {viewpointLightingTools.length ? (
                            <NodeDockMenuButton menuId="viewpoint-lighting" label={isImage ? "视角调整" : "视角"} icon={<Camera className="size-3.5" />} tools={viewpointLightingTools} openMenuId={openMenuId} onOpenChange={handleMenuOpenChange} />
                        ) : null}
                        {processTools.length ? (
                            <NodeDockMenuButton
                                menuId="process"
                                label={processMenuLabel}
                                icon={isImage ? <Images className="size-3.5" /> : <SlidersHorizontal className="size-3.5" />}
                                tools={processTools}
                                openMenuId={openMenuId}
                                onOpenChange={handleMenuOpenChange}
                                split={hasImage && !simpleMode ? { node, onSplit } : undefined}
                            />
                        ) : null}
                        {imageSettingsTools.length ? <NodeDockMenuButton menuId="image-settings" label="图片设置" icon={<Settings2 className="size-3.5" />} tools={imageSettingsTools} openMenuId={openMenuId} onOpenChange={handleMenuOpenChange} /> : null}
                        {workspaceTools.length ? <span aria-hidden className="aceternity-dock-separator mx-1 h-5 w-px shrink-0" /> : null}
                        {workspaceTools.map((tool) => (
                            <NodeDockToolButton key={tool.id} tool={tool} />
                        ))}
                        {utilityTools.length ? <span aria-hidden className="aceternity-dock-separator mx-1 h-5 w-px shrink-0" /> : null}
                        {utilityTools.map((tool) => (
                            <NodeDockToolButton key={tool.id} tool={tool} iconOnly />
                        ))}
                    </>
                )}
            </div>
        </div>
    );
}

function UpscaleToolbarIcon() {
    return <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="#fff" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M6 2H3a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1v-3" />
        <path d="M9 2h5v5M14 2 7 9" />
    </svg>;
}

export function CanvasNodeInfoModal({
    node,
    open,
    onClose,
    onMetadataChange,
    readOnly = false,
    onUnauthorized,
}: {
    node: CanvasNodeData | null;
    open: boolean;
    onClose: () => void;
    onMetadataChange?: (nodeId: string, metadata: Partial<CanvasNodeMetadata>) => void;
    readOnly?: boolean;
    onUnauthorized?: () => void;
}) {
    const theme = canvasThemes[useActiveTheme()];
    const [assetTags, setAssetTags] = useState<string[]>([]);
    const [assetTagInput, setAssetTagInput] = useState("");
    const [assetCategory, setAssetCategory] = useState<CanvasAssetCategory>("other");
    const imageBytes = node?.type === CanvasNodeType.Image && node.metadata?.content ? getDataUrlByteSize(node.metadata.content) : 0;
    const batchCount = node?.type === CanvasNodeType.Image ? node.metadata?.batchChildIds?.length || 0 : 0;
    const nodeTypeLabel =
        node?.type === CanvasNodeType.Text
            ? "文本"
            : node?.type === CanvasNodeType.Script
              ? "分镜脚本"
              : node?.type === CanvasNodeType.Skill
                ? "技能"
                : node?.type === CanvasNodeType.Image
                  ? "图片"
                  : node?.type === CanvasNodeType.Video
                    ? "视频"
                    : node?.type === CanvasNodeType.Audio
                      ? "音频"
                      : node?.type === CanvasNodeType.Drawing
                        ? "绘图"
                        : node?.type === CanvasNodeType.Frame
                          ? "背板"
                          : "生成配置";
    useEffect(() => {
        setAssetTags(node?.metadata?.assetTags || []);
        setAssetTagInput("");
        setAssetCategory(node ? canvasNodeAssetCategory(node) : "other");
    }, [node?.id, node?.metadata?.assetCategory, node?.metadata?.assetTags]);

    const saveAssetCategory = (category: CanvasAssetCategory) => {
        if (!node || node.type !== CanvasNodeType.Image) return;
        setAssetCategory(category);
        onMetadataChange?.(node.id, { assetCategory: category });
    };

    const saveAssetTags = (nextTags: string[]) => {
        if (!node || node.type !== CanvasNodeType.Image) return;
        const tags = Array.from(new Set(nextTags.map((item) => item.trim()).filter(Boolean)));
        setAssetTags(tags);
        onMetadataChange?.(node.id, { assetTags: tags });
    };

    const addAssetTag = () => {
        const tags = assetTagInput
            .split(/\n|,|，/)
            .map((item) => item.trim())
            .filter(Boolean);
        if (!tags.length) return;
        saveAssetTags([...assetTags, ...tags]);
        setAssetTagInput("");
    };

    const removeAssetTag = (tag: string) => {
        saveAssetTags(assetTags.filter((item) => item !== tag));
    };

    const title = (
        <div className="canvas-node-inspector-title">
            <div className="min-w-0">
                <div className="text-[var(--fs-heading-lg)] font-semibold">节点信息</div>
                {node ? <div className="canvas-node-inspector-id">{node.id}</div> : null}
            </div>
        </div>
    );

    return (
        <Modal className="workspace-modal canvas-node-info-modal" title={title} open={open && Boolean(node)} centered footer={null} onCancel={onClose} width="min(920px, calc(100vw - 32px))" styles={{ body: { paddingTop: 4 } }}>
            {node ? (
                <div className="canvas-node-inspector" style={{ color: theme.node.text }}>
                    <div className="thin-scrollbar canvas-node-inspector-scroll">
                        <section className="canvas-node-inspector-section">
                            <div className="canvas-node-inspector-section-heading">
                                <span>基础信息</span>
                                <em>{node.metadata?.status || "idle"}</em>
                            </div>
                            <div className="canvas-node-inspector-facts">
                                <InfoRow label="类型" value={nodeTypeLabel} />
                                <InfoRow label="尺寸" value={`${Math.round(node.width)} x ${Math.round(node.height)}`} />
                                <InfoRow label="位置" value={`${Math.round(node.position.x)}, ${Math.round(node.position.y)}`} />
                                {batchCount > 1 ? <InfoRow label="图片组" value={`${batchCount} 张`} /> : null}
                                {imageBytes ? <InfoRow label="图片大小" value={formatBytes(imageBytes)} /> : null}
                            </div>
                        </section>

                        {node.type === CanvasNodeType.Image ? (
                            <section className="canvas-node-inspector-section">
                                <div className="canvas-node-inspector-section-heading">
                                    <span>项目资产分类</span>
                                </div>
                                <div className="canvas-node-inspector-options">
                                    {assetCategoryOptions.map((option) => {
                                        const active = assetCategory === option.value;
                                        return (
                                            <button key={option.value} type="button" disabled={readOnly} aria-pressed={active} onClick={() => saveAssetCategory(option.value)} className={active ? "is-active" : ""}>
                                                {option.label}
                                            </button>
                                        );
                                    })}
                                </div>
                                <p className="canvas-node-inspector-help">生成后会按此分类进入项目资产；角色、场景和画风工作流会自动预填。</p>
                            </section>
                        ) : null}

                        {node.metadata?.prompt ? (
                            <section className="canvas-node-inspector-section">
                                <div className="canvas-node-inspector-section-heading">
                                    <span>提示词</span>
                                </div>
                                <div className="canvas-node-inspector-copy canvas-node-inspector-prompt">{node.metadata.prompt}</div>
                            </section>
                        ) : null}

                        {nodeGenerationRows(node).length ? (
                            <section className="canvas-node-inspector-section">
                                <div className="canvas-node-inspector-section-heading">
                                    <span>生成信息</span>
                                </div>
                                <div className="canvas-node-inspector-facts">
                                    {nodeGenerationRows(node).map((item) => (
                                        <InfoRow key={item.label} label={item.label} value={item.value} />
                                    ))}
                                </div>
                            </section>
                        ) : null}

                        {node.type === CanvasNodeType.Skill && node.metadata?.skillSnapshot ? (
                            <section className="canvas-node-inspector-section">
                                <div className="canvas-node-inspector-section-heading">
                                    <span>技能模板</span>
                                </div>
                                <div className="canvas-node-inspector-copy">{node.metadata.skillSnapshot.template}</div>
                                {node.metadata.skillSnapshot.outputContract ? (
                                    <>
                                        <div className="canvas-node-inspector-subheading">输出约束</div>
                                        <div className="canvas-node-inspector-copy">{node.metadata.skillSnapshot.outputContract}</div>
                                    </>
                                ) : null}
                            </section>
                        ) : null}

                        {node.type === CanvasNodeType.Image ? (
                            <section className="canvas-node-inspector-section">
                                <div className="canvas-node-inspector-section-heading">
                                    <div>
                                        <span>资产标签</span>
                                        <p>一条标签描述一个角色、环境、道具或镜头用途。</p>
                                    </div>
                                    <em>{assetTags.length} 条</em>
                                </div>
                                {readOnly ? (
                                    <div className="canvas-node-inspector-notice">分享画布为只读，标签无法编辑。</div>
                                ) : (
                                    <div className="canvas-node-inspector-tag-editor">
                                        <Input value={assetTagInput} placeholder="例如：角色: 张三" onChange={(event) => setAssetTagInput(event.target.value)} onPressEnter={addAssetTag} />
                                        <Button type="primary" icon={<Plus className="size-4" />} disabled={!assetTagInput.trim()} onClick={addAssetTag}>
                                            加入
                                        </Button>
                                    </div>
                                )}
                                <div className="canvas-node-inspector-tags">
                                    {assetTags.length ? (
                                        assetTags.map((tag) => (
                                            <Tag key={tag} closable={!readOnly} onClose={() => (readOnly ? onUnauthorized?.() : removeAssetTag(tag))} className="!m-0 !rounded-lg !px-2 !py-1 !text-sm">
                                                {tag}
                                            </Tag>
                                        ))
                                    ) : (
                                        <span className="canvas-node-inspector-empty-label">{readOnly ? "暂无标签" : "还没有标签，输入后点击“加入”或按 Enter。"}</span>
                                    )}
                                </div>
                            </section>
                        ) : null}

                        {node.metadata?.errorDetails ? (
                            <section className="canvas-node-inspector-error">
                                <GenerationFailureNotice
                                    explanation={explainGenerationError(
                                        { code: node.metadata.generationErrorCode || node.metadata.taskErrorCode, message: node.metadata.errorDetails },
                                        { taskId: node.metadata.taskId, model: node.metadata.model, createdAt: node.metadata.taskCreatedAt, stage: node.metadata.taskStage },
                                    )}
                                    context={{ ...canvasNodeDiagnosticContext(node), model: node.metadata.model, createdAt: node.metadata.taskCreatedAt, stage: node.metadata.taskStage, providerRequestId: node.metadata.taskProviderRequestId, failureDiagnostics: node.metadata.taskFailureDiagnostics, errorSummary: node.metadata.generationErrorSummary, providerCode: node.metadata.generationProviderCode, completedAt: node.metadata.taskCompletedAt, updatedAt: node.metadata.taskUpdatedAt }}
                                />
                            </section>
                        ) : null}
                    </div>
                </div>
            ) : null}
        </Modal>
    );
}

function InfoRow({ label, value }: { label: string; value: ReactNode }) {
    return (
        <div className="canvas-node-inspector-fact">
            <div>{label}</div>
            <strong>{value}</strong>
        </div>
    );
}

function nodeGenerationRows(node: CanvasNodeData) {
    const metadata = node.metadata;
    if (!metadata) return [] as Array<{ label: string; value: string }>;
    const rows: Array<{ label: string; value: string }> = [];
    const add = (label: string, value: unknown) => {
        if (value === undefined || value === null || value === "") return;
        rows.push({ label, value: String(value) });
    };
    const addTime = (label: string, value?: string) => {
        if (!value) return;
        const timestamp = Date.parse(value);
        add(label, Number.isFinite(timestamp) ? new Date(timestamp).toLocaleString() : value);
    };
    const addDuration = (value?: number) => {
        if (typeof value !== "number" || !Number.isFinite(value)) return;
        const totalSeconds = Math.max(0, Math.round(value / 1000));
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = totalSeconds % 60;
        add("耗时", minutes ? `${minutes}分 ${seconds}秒` : `${seconds}秒`);
    };

    add("模型", metadata.model);
    add("生成尺寸", metadata.size);
    add("分辨率", metadata.vquality || metadata.quality);
    add("秒数", metadata.seconds ? `${metadata.seconds} 秒` : undefined);
    add("生成声音", metadata.generateAudio === undefined ? undefined : metadata.generateAudio === "true" ? "开启" : "关闭");
    add("水印", metadata.watermark === undefined ? undefined : metadata.watermark === "true" ? "开启" : "关闭");
    if (metadata.references?.length) {
        const referenceNames = metadata.references
            .slice(0, 3)
            .map((reference) => reference.split("/").pop() || reference)
            .join("、");
        add("引用素材", `${metadata.references.length} 个${referenceNames ? `（${referenceNames}${metadata.references.length > 3 ? "…" : ""}）` : ""}`);
    }
    addTime("创建时间", metadata.taskCreatedAt);
    addTime("开始时间", metadata.taskStartedAt);
    addTime("完成时间", metadata.taskCompletedAt);
    addDuration(metadata.taskDurationMs);
    return rows;
}
