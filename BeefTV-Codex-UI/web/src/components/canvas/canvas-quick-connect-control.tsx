import { useId } from "react";
import { Link2 } from "lucide-react";
import { Tooltip } from "@/components/ui/base/tooltip";
import type { canvasThemes } from "@/lib/canvas-theme";

type CanvasQuickConnectControlProps = {
    active: boolean;
    onToggle: () => void;
    theme: (typeof canvasThemes)[keyof typeof canvasThemes];
};

export function CanvasQuickConnectControl({ active, onToggle, theme }: CanvasQuickConnectControlProps) {
    const helpId = useId();
    const hint = active
        ? "已固定当前目标，点击画布素材或组可连续引用；按 Esc 或再次点击退出"
        : "固定当前目标，点击画布素材或组即可引用，可连续添加多个";
    return (
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1" data-canvas-no-drag>
            <Tooltip title={hint}>
                <button
                    type="button"
                    aria-label="快速连线"
                    aria-pressed={active}
                    aria-describedby={active ? helpId : undefined}
                    className="inline-flex h-6 shrink-0 items-center gap-1 rounded-md border px-1.5 text-[var(--fs-tiny)] font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
                    style={{ color: active ? theme.toolbar.activeText : theme.node.muted, background: active ? theme.toolbar.activeBg : "transparent", borderColor: active ? theme.node.activeStroke : theme.toolbar.border, outlineColor: theme.node.activeStroke }}
                    onPointerDown={(event) => event.stopPropagation()}
                    onClick={(event) => { event.stopPropagation(); onToggle(); }}
                >
                    <Link2 className="size-3" aria-hidden="true" />
                    <span>快速连线</span>
                    {active ? <span aria-hidden="true">· 已开启</span> : null}
                </button>
            </Tooltip>
            {active ? (
                <span id={helpId} role="status" aria-label="快速连线状态" className="min-w-0 flex-1 text-[var(--fs-tiny)] leading-4" style={{ color: theme.node.muted }}>
                    已开启，点击素材或组可连续引用。按 Esc 或再次点击退出。
                </span>
            ) : null}
        </div>
    );
}
