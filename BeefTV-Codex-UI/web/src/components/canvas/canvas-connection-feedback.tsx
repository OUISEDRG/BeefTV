import { CheckCircle2, Info, TriangleAlert, X } from "lucide-react";
import type { CanvasTheme } from "@/lib/canvas-theme";

export type CanvasConnectionFeedback = { kind: "success" | "warning" | "info"; message: string };

export function CanvasConnectionFeedbackNotice({ feedback, onDismiss, theme }: { feedback: CanvasConnectionFeedback | null; onDismiss: () => void; theme: CanvasTheme }) {
    if (!feedback) return null;
    const Icon = feedback.kind === "warning" ? TriangleAlert : feedback.kind === "success" ? CheckCircle2 : Info;
    return <div
        data-canvas-connection-feedback
        data-canvas-no-zoom
        role={feedback.kind === "warning" ? "alert" : "status"}
        aria-live={feedback.kind === "warning" ? "assertive" : "polite"}
        className="absolute left-1/2 top-14 z-[var(--z-panel-floating)] flex max-w-[calc(100%-32px)] -translate-x-1/2 items-center gap-2 rounded-xl border px-3 py-2 text-sm shadow-lg"
        style={{ background: theme.spatial.elevated, borderColor: theme.toolbar.border, color: theme.node.text }}
        onPointerDown={(event) => event.stopPropagation()}
        onMouseDown={(event) => event.stopPropagation()}
    >
        <Icon aria-hidden="true" className="size-4 shrink-0" style={{ color: feedback.kind === "warning" ? theme.accent.danger : theme.node.activeStroke }} />
        <span>{feedback.message}</span>
        <button type="button" aria-label="关闭连线提示" onClick={onDismiss} className="grid size-7 shrink-0 place-items-center rounded-md hover:bg-white/10 focus-visible:outline focus-visible:outline-2"><X aria-hidden="true" className="size-4" /></button>
    </div>;
}
