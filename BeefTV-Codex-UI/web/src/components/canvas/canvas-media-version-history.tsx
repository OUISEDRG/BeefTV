import { useEffect, useRef, useState } from "react";
import { Check, Download, History, Image as ImageIcon, Music2, Play, Video, X } from "lucide-react";

import { CachedResourceImage } from "@/components/cached-resource-image";
import { VideoPlayer } from "@/components/video-player";
import { useResourceVideoPlayback } from "@/hooks/use-resource-video-playback";
import { canvasNodeVideoPreviewUrl } from "@/lib/canvas/canvas-media-preview";
import { isCanvasNodeGenerating } from "@/lib/canvas/canvas-node-task-state";
import { mediaVersionNumber } from "@/lib/canvas/canvas-media-versions";
import { resolveMediaUrl } from "@/services/file-storage";
import type { CanvasTheme } from "@/lib/canvas-theme";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";
import { useCanvasNodeActions } from "./canvas-node-action-context";

export function CanvasMediaVersionHistory({ node, versions, theme, readOnly, onViewImage }: {
    node: CanvasNodeData; versions: CanvasNodeData[]; theme: CanvasTheme; readOnly: boolean;
    onViewImage?: (node: CanvasNodeData) => void;
}) {
    const [open, setOpen] = useState(false);
    const [playingId, setPlayingId] = useState<string | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const toggleRef = useRef<HTMLButtonElement>(null);
    const { useMediaVersion, download } = useCanvasNodeActions();
    const hasCurrent = Boolean(node.metadata?.content || node.metadata?.storageKey);
    const history = versions.filter(version => Boolean(version.metadata?.content || version.metadata?.storageKey)
        && !(hasCurrent && (node.metadata?.storageKey ? version.metadata?.storageKey === node.metadata.storageKey : version.metadata?.content === node.metadata?.content)));
    const items = [...(hasCurrent ? [node] : []), ...history].sort((a, b) => mediaVersionNumber(b.metadata?.versionLabel) - mediaVersionNumber(a.metadata?.versionLabel));
    const busy = isCanvasNodeGenerating(node);

    useEffect(() => {
        if (!open) { setPlayingId(null); return; }
        const outside = (event: PointerEvent) => {
            if (event.target instanceof Node && !containerRef.current?.contains(event.target)) setOpen(false);
        };
        const escape = (event: KeyboardEvent) => {
            if (event.key !== "Escape") return;
            event.stopPropagation();
            setOpen(false);
            toggleRef.current?.focus();
        };
        document.addEventListener("pointerdown", outside, true);
        document.addEventListener("keydown", escape, true);
        return () => { document.removeEventListener("pointerdown", outside, true); document.removeEventListener("keydown", escape, true); };
    }, [open]);
    if (items.length < 2) return null;
    return <div ref={containerRef} className="absolute right-3 top-3 z-[var(--node-z-overlay)]" data-canvas-no-zoom
        onMouseDown={event => event.stopPropagation()} onPointerDown={event => event.stopPropagation()}
        onClick={event => event.stopPropagation()} onDoubleClick={event => event.stopPropagation()} onContextMenu={event => event.stopPropagation()}>
        <button ref={toggleRef} type="button" aria-label={`查看 ${items.length} 个版本`} aria-expanded={open} aria-controls={`media-history-${node.id}`}
            className="inline-flex h-7 items-center gap-1.5 rounded-[var(--r-md)] border px-2 text-[var(--node-badge-fs)] font-medium backdrop-blur-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
            style={{ background: theme.toolbar.panel, borderColor: theme.toolbar.border, color: theme.node.text, outlineColor: theme.accent.primary }} onClick={() => setOpen(value => !value)}>
            <History className="size-3.5" />{items.length} 个版本
        </button>
        {open ? <aside id={`media-history-${node.id}`} role="region" aria-label="历史版本" data-canvas-wheel-scroll
            className="absolute top-0 w-[220px] overflow-hidden rounded-[var(--r-lg)] border shadow-xl"
            style={{ left: "calc(100% + 24px)", background: theme.node.panel, borderColor: theme.toolbar.border, color: theme.node.text }}
            onWheel={event => event.stopPropagation()}>
            <div className="flex items-center justify-between border-b px-3 py-2" style={{ borderColor: theme.toolbar.border }}>
                <span className="text-xs font-medium">历史版本 · {items.length}</span>
                <button type="button" aria-label="收起历史版本" className="grid size-6 place-items-center rounded-[var(--r-sm)]" onClick={() => { setOpen(false); toggleRef.current?.focus(); }}><X className="size-3.5" /></button>
            </div>
            <div className="thin-scrollbar flex max-h-[420px] flex-col gap-2 overflow-y-auto p-2">
                {items.map(version => {
                    const current = version.id === node.id;
                    const label = `v${mediaVersionNumber(version.metadata?.versionLabel)}`;
                    return <article key={version.id} className="shrink-0 overflow-hidden rounded-[var(--r-md)] border" style={{ borderColor: current ? theme.accent.primary : theme.toolbar.border }}>
                        <MediaVersionPreview node={version} theme={theme} active={playingId === version.id}
                            onPlay={() => setPlayingId(version.id)} onViewImage={onViewImage} label={label} />
                        <div className="flex items-center justify-between gap-1 px-2 py-1.5 text-xs">
                            <span>{label}</span>
                            <div className="flex items-center gap-1">
                                {download ? <button type="button" aria-label={`下载版本 ${label}`} className="grid size-6 place-items-center rounded-[var(--r-sm)]" onClick={() => download(version)}><Download className="size-3.5" /></button> : null}
                                {current ? <span className="inline-flex items-center gap-1" style={{ color: theme.accent.primary }}><Check className="size-3" />当前版本</span>
                                    : !readOnly && useMediaVersion ? <button type="button" disabled={busy} title={busy ? "当前任务完成后可切换版本" : undefined}
                                        className="rounded-[var(--r-sm)] border px-1.5 py-1 disabled:cursor-wait disabled:opacity-50" style={{ borderColor: theme.toolbar.border }}
                                        aria-label={`使用版本 ${label}`} onClick={() => { setPlayingId(null); useMediaVersion(version.id); }}>使用此版本</button> : null}
                            </div>
                        </div>
                    </article>;
                })}
            </div>
        </aside> : null}
    </div>;
}

function MediaVersionPreview({ node, theme, active, onPlay, onViewImage, label }: {
    node: CanvasNodeData; theme: CanvasTheme; active: boolean; onPlay: () => void;
    onViewImage?: (node: CanvasNodeData) => void; label: string;
}) {
    const playback = useResourceVideoPlayback(node.metadata?.storageKey || "", node.metadata?.content || "", active && node.type === CanvasNodeType.Video);
    const [audioUrl, setAudioUrl] = useState("");
    const [audioError, setAudioError] = useState(false);
    useEffect(() => {
        if (!active || node.type !== CanvasNodeType.Audio) return;
        let canceled = false;
        setAudioError(false);
        void resolveMediaUrl(node.metadata?.storageKey || "", node.metadata?.content || "").then(url => { if (!canceled) setAudioUrl(url); }).catch(() => { if (!canceled) setAudioError(true); });
        return () => { canceled = true; };
    }, [active, node.type, node.metadata?.storageKey, node.metadata?.content]);
    const Icon = node.type === CanvasNodeType.Audio ? Music2 : node.type === CanvasNodeType.Video ? Video : ImageIcon;
    const fallback = <span className="grid h-full w-full place-items-center"><Icon className="size-7" style={{ color: theme.node.muted }} /></span>;
    return <div className="relative flex h-[124px] items-center justify-center overflow-hidden" style={{ background: theme.toolbar.itemHover }}>
        {node.type === CanvasNodeType.Image ? <button type="button" className="h-full w-full" aria-label={`预览版本 ${label}`} onClick={() => onViewImage?.(node)}>
            <CachedResourceImage src={node.metadata?.content} storageKey={node.metadata?.storageKey} alt={`版本 ${label}`} className="h-full w-full object-contain" fallback={fallback} />
        </button> : node.type === CanvasNodeType.Video && active ? <div className="h-full w-full">
            {playback.url ? <VideoPlayer src={playback.url} autoPlay className="h-full w-full" /> : <span className="p-2 text-xs">{playback.error || "正在载入视频…"}</span>}
        </div> : node.type === CanvasNodeType.Audio && active ? <div className="w-full px-2">{audioUrl ? <audio controls autoPlay src={audioUrl} className="w-full" /> : <span className="text-xs">{audioError ? "音频加载失败" : "正在载入音频…"}</span>}</div>
            : <button type="button" onClick={onPlay} aria-label={`播放版本 ${label}`} className="relative h-full w-full">
                {node.type === CanvasNodeType.Video ? <CachedResourceImage src={canvasNodeVideoPreviewUrl(node)} storageKey={node.metadata?.videoPreview?.storageKey} alt={`版本 ${label} 封面`} className="h-full w-full object-cover" fallback={fallback} /> : fallback}
                <span className="absolute inset-0 grid place-items-center"><Play className="size-8 rounded-full p-1.5" style={{ background: theme.toolbar.panel, color: theme.node.text }} /></span>
            </button>}
    </div>;
}
