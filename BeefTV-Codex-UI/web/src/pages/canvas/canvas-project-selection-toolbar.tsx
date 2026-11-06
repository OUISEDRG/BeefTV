import type { RefObject } from "react";

import { CanvasSelectionToolbar } from "@/components/canvas/canvas-workspace-overlays";
import { FloatingDock } from "@/components/ui/aceternity/floating-dock";
import { canvasThemes } from "@/lib/canvas-theme";
import { canvasDockStyle } from "@/lib/canvas/canvas-aceternity-style";
import { defaultToolbarPrefs, readToolbarPrefs, resolveToolbarEntries, type ToolContext, type ToolbarHandlers } from "@/lib/canvas/tool-registry";
import type { CanvasAlignmentMode } from "@/lib/canvas/canvas-layout";
import { selectStoryboardGroupMembers } from "@/lib/canvas/canvas-storyboard-group";
import type { CanvasNodeData } from "@/types/canvas";
import { useCanvasThemeStore } from "@/stores/canvas/use-canvas-theme-store";

type CanvasProjectSelectionToolbarProps = {
    anchorRef: RefObject<HTMLDivElement | null>;
    containerRef: RefObject<HTMLDivElement | null>;
    count: number;
    nodes: CanvasNodeData[];
    selectedNodeIds: ReadonlySet<string>;
    defaultSeriesId?: number;
    selectedVideoCount: number;
    mergingVideos: boolean;
    onAlign: (mode: CanvasAlignmentMode) => void;
    onArrange: (mode: "row" | "column" | "grid" | "flow") => void;
    onCreateStoryboard: () => void;
    onCreateReferenceGroup: () => void;
    onBatchConnect: () => void;
    onMergeVideos: () => void;
    onAskAssistant: () => void;
    onSaveWorkflow?: () => void;
};

export function CanvasProjectSelectionToolbar({ anchorRef, containerRef, count, nodes, selectedNodeIds, defaultSeriesId, selectedVideoCount, mergingVideos, onAlign, onArrange, onCreateStoryboard, onCreateReferenceGroup, onBatchConnect, onMergeVideos, onAskAssistant, onSaveWorkflow }: CanvasProjectSelectionToolbarProps) {
    const theme = canvasThemes[useCanvasThemeStore((state) => state.theme)];
    const storyboardSelection = selectStoryboardGroupMembers(nodes, selectedNodeIds, defaultSeriesId);
    const storyboardDisabledReason = storyboardSelection.ok ? "" : storyboardSelection.reason;

    const handlers = {
        onAlign, onArrange, onCreateStoryboard, onCreateReferenceGroup, onBatchConnect, onMergeVideos, onAskAssistant, onSaveWorkflow,
    } as Partial<ToolbarHandlers> as ToolbarHandlers;

    const ctx: ToolContext = {
        selectedCount: count,
        selectedNodeTypes: new Set(nodes.filter(node => selectedNodeIds.has(node.id)).map(node => node.type)),
        selectedVideoCount,
        storyboardDisabledReason,
        canvasTool: "move",
        workspaceMode: "professional",
        isProjectLinked: false,
        canUndo: false,
        canRedo: false,
        extractingVideoFrames: false,
        extractingAudio: false,
        trimmingVideo: false,
        mergingVideos,
        addPanelOpen: false,
        appearancePanelOpen: false,
        settingsPanelOpen: false,
        handlers,
    };

    const prefs = readToolbarPrefs("selection") ?? defaultToolbarPrefs("selection");
    const items = resolveToolbarEntries("selection", ctx, prefs);

    return (
        <CanvasSelectionToolbar anchorRef={anchorRef} containerRef={containerRef} count={count}>
            <FloatingDock items={items} size="compact" className="canvas-floating-dock" style={canvasDockStyle(theme)} ariaLabel="多选节点布局工具" />
            {storyboardDisabledReason ? <div role="status" className="mt-1 rounded-[var(--r-sm)] border px-2 py-1 text-[var(--fs-tiny)]" style={{ background: theme.spatial.elevated, borderColor: theme.toolbar.border, color: theme.node.text }}>分镜组：{storyboardDisabledReason}</div> : null}
        </CanvasSelectionToolbar>
    );
}
