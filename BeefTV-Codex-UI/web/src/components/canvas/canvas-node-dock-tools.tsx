import { useEffect, useRef, useState, type ReactNode } from "react";
import { Dropdown, Tooltip, type MenuProps } from "antd";
import { ChevronDown, ChevronRight } from "lucide-react";

import type { NodeToolbarGroup } from "@/lib/canvas/tool-registry";
import type { CanvasNodeData } from "@/types/canvas";
import type { ImageSplitParams } from "@/lib/canvas/canvas-image-data";
import { CanvasGridSplitPicker } from "./canvas-grid-split-picker";

export type ToolbarTool = {
    section?: string;
    description?: string;
    id: string;
    label: string;
    icon: ReactNode;
    onClick: () => void;
    group: NodeToolbarGroup;
    order: number;
    active?: boolean;
    danger?: boolean;
    disabled?: boolean;
    disabledReason?: string;
};

export function NodeDockToolButton({ tool, iconOnly = false, showTooltip = true }: { tool: ToolbarTool; iconOnly?: boolean; showTooltip?: boolean }) {
    const button = (
        <button
            type="button"
            className={`aceternity-dock-command is-labeled pointer-events-auto inline-flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-[var(--dock-item-radius)] px-2.5 outline-none ${tool.active ? "is-active" : ""} ${tool.danger ? "is-danger" : ""}`}
            aria-label={tool.label}
            aria-description={tool.disabledReason}
            title={tool.disabledReason}
            aria-pressed={tool.active}
            disabled={tool.disabled}
            onClick={() => { if (!tool.disabled) tool.onClick(); }}
        >
            <span className="grid size-3.5 shrink-0 place-items-center">{tool.icon}</span>
            {!iconOnly ? <span className="inline-flex h-4 items-center whitespace-nowrap text-[var(--fs-label)] font-medium leading-none">{tool.label}</span> : null}
        </button>
    );
    return showTooltip || tool.disabledReason ? <Tooltip title={tool.disabledReason || (tool.description ? `${tool.label}：${tool.description}` : tool.label)}>{button}</Tooltip> : button;
}

export function compareToolbarTools(left: ToolbarTool, right: ToolbarTool) {
    if (left.danger !== right.danger) return left.danger ? 1 : -1;
    return left.order - right.order;
}

export function NodeDockMenuButton({
    menuId,
    label,
    icon,
    tools,
    openMenuId,
    onOpenChange,
    placement = "bottom",
    iconOnly = false,
    split,
}: {
    menuId: string;
    label: string;
    icon?: ReactNode;
    tools: ToolbarTool[];
    openMenuId: string | null;
    onOpenChange: (menuId: string, open: boolean) => void;
    placement?: "top" | "topRight" | "bottom" | "bottomLeft" | "bottomRight";
    iconOnly?: boolean;
    split?: { node: CanvasNodeData; onSplit: (node: CanvasNodeData, params: ImageSplitParams) => void };
}) {
    const open = openMenuId === menuId;
    const triggerRef = useRef<HTMLButtonElement>(null);
    const [splitPanelOpen, setSplitPanelOpen] = useState(false);
    useEffect(() => {
        if (!open) {
            setSplitPanelOpen(false);
            return;
        }
        const frame = requestAnimationFrame(() => triggerRef.current?.focus());
        return () => cancelAnimationFrame(frame);
    }, [open]);
    const keepSplitMenuOpenRef = useRef(false);
    const splitEntry = split ? tools.find((tool) => tool.id === "split") : undefined;
    const renderItem = (tool: ToolbarTool): NonNullable<MenuProps["items"]>[number] => {
        const isSplit = Boolean(splitEntry && split && tool.id === "split");
        return {
            key: tool.id,
            icon: undefined,
            className: isSplit ? `canvas-grid-split-menu-item${splitPanelOpen ? " is-open" : ""}` : undefined,
            label: (
                <div
                    className={isSplit ? "canvas-grid-split-menu-label" : undefined}
                    title={tool.disabledReason}
                    aria-description={tool.disabledReason}
                    onMouseDown={
                        isSplit
                            ? () => {
                                  keepSplitMenuOpenRef.current = true;
                              }
                            : undefined
                    }
                >
                    <div>
                        <span className="inline-flex items-center gap-2">{tool.label}</span>
                        {tool.disabledReason ? <span className="mt-1 block max-w-72 whitespace-normal text-[var(--fs-tiny)] leading-relaxed">{tool.disabledReason}</span> : null}
                        {!tool.disabledReason && tool.description === "基于原图重新生成" ? <span className="mt-1 block text-[var(--fs-tiny)]">{tool.description}</span> : null}
                    </div>
                    {isSplit ? <ChevronRight className="canvas-grid-split-chevron" strokeWidth={2} /> : null}
                </div>
            ),
            disabled: tool.disabled,
            danger: tool.danger,
            onClick: (info) => {
                if (tool.disabled) return;
                if (isSplit) {
                    info.domEvent.preventDefault();
                    info.domEvent.stopPropagation();
                    keepSplitMenuOpenRef.current = true;
                    setSplitPanelOpen((current) => !current);
                    return;
                }
                onOpenChange(menuId, false);
                tool.onClick();
            },
        };
    };
    const items: MenuProps["items"] = tools.map(renderItem);
    return (
        <Dropdown
            open={open}
            trigger={["click"]}
            placement={placement}
            autoAdjustOverflow={false}
            onOpenChange={(nextOpen) => {
                if (!nextOpen && (keepSplitMenuOpenRef.current || document.querySelector(".canvas-node-toolbar-menu-split:hover, .canvas-grid-split-picker:hover"))) {
                    keepSplitMenuOpenRef.current = false;
                    return;
                }
                onOpenChange(menuId, nextOpen);
                if (!nextOpen) setSplitPanelOpen(false);
            }}
            menu={{ items }}
            autoFocus
            popupRender={(menu) => (
                <div
                    className={`canvas-node-toolbar-menu${splitEntry && split ? " canvas-node-toolbar-menu-split" : ""}`}
                    data-canvas-no-zoom
                    data-canvas-wheel-scroll
                    onPointerDown={(event) => event.stopPropagation()}
                    onMouseDown={(event) => event.stopPropagation()}
                    onWheel={(event) => event.stopPropagation()}
                    onKeyDownCapture={(event) => {
                        if (event.key === "Escape") {
                            event.preventDefault();
                            event.stopPropagation();
                            triggerRef.current?.focus();
                            onOpenChange(menuId, false);
                        }
                    }}
                    onKeyDown={(event) => event.stopPropagation()}
                >
                    <div className="canvas-node-toolbar-menu-stack">{menu}</div>
                    {splitPanelOpen && split ? (
                        <CanvasGridSplitPicker
                            onPick={(params) => {
                                setSplitPanelOpen(false);
                                onOpenChange(menuId, false);
                                split.onSplit(split.node, params);
                            }}
                        />
                    ) : null}
                </div>
            )}
        >
            <button
                ref={triggerRef}
                type="button"
                className={`aceternity-dock-command is-labeled pointer-events-auto inline-flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-[var(--dock-item-radius)] px-2.5 outline-none ${open ? "is-active" : ""}`}
                aria-label={label}
                aria-expanded={open}
                aria-haspopup="menu"
                title={label}
                onKeyDown={(event) => {
                    if (event.key === "ArrowDown" && open) {
                        event.preventDefault();
                        document.querySelector<HTMLElement>(".ant-dropdown:not(.ant-dropdown-hidden) .canvas-node-toolbar-menu [role='menuitem']:not([aria-disabled='true'])")?.focus();
                    }
                    if (event.key === "Escape" && open) {
                        event.preventDefault();
                        event.stopPropagation();
                        onOpenChange(menuId, false);
                    }
                }}
            >
                {icon ? <span className="grid size-3.5 shrink-0 place-items-center">{icon}</span> : null}
                {!iconOnly ? (
                    <>
                        <span className="inline-flex h-4 items-center whitespace-nowrap text-[var(--fs-label)] font-medium leading-none">{label}</span>
                        <ChevronDown className="size-3 shrink-0 opacity-55" />
                    </>
                ) : null}
            </button>
        </Dropdown>
    );
}
