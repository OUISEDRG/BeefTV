import { useCallback, useEffect, useLayoutEffect, useRef, useState, type Dispatch, type RefObject, type SetStateAction } from "react";
import type { CanvasConnectionFeedback } from "@/components/canvas/canvas-connection-feedback";
import { canvasReferenceSourceRestriction, planCanvasReferenceConnections } from "@/lib/canvas/canvas-group-references";
import { canOpenCanvasNodePromptPanel } from "@/lib/canvas/canvas-node-semantics";
import type { AiConfig } from "@/stores/use-config-store";
import { CanvasNodeType, type CanvasConnection, type CanvasNodeData } from "@/types/canvas";

type Options = {
    projectId: string;
    dialogNodeId: string | null;
    readOnly: boolean;
    config: AiConfig;
    containerRef: RefObject<HTMLDivElement | null>;
    nodes: CanvasNodeData[];
    nodesRef: { current: CanvasNodeData[] };
    connectionsRef: { current: CanvasConnection[] };
    setConnections: Dispatch<SetStateAction<CanvasConnection[]>>;
    cancelConnectionInteraction: () => void;
    onFeedback: (feedback: CanvasConnectionFeedback) => void;
};

type Session = { projectId: string; targetNodeId: string };
type PointerInput = { button: number; preventDefault(): void; stopPropagation(): void };

/** The composer owns the target; selecting a source must never switch its panel. */
export function useCanvasQuickConnect(options: Options) {
    const [session, setSession] = useState<Session | null>(null);
    const sessionRef = useRef(session);
    const optionsRef = useRef(options);
    useLayoutEffect(() => { optionsRef.current = options; });

    const stop = useCallback(() => {
        sessionRef.current = null;
        setSession(null);
    }, []);

    const activeTargetNodeId = session && !options.readOnly
        && session.projectId === options.projectId && session.targetNodeId === options.dialogNodeId
        && options.nodes.some(node => node.id === session.targetNodeId)
        ? session.targetNodeId : null;

    useLayoutEffect(() => {
        if (session && !activeTargetNodeId) stop();
    }, [activeTargetNodeId, session, stop]);

    const activeTarget = useCallback(() => {
        const current = optionsRef.current;
        const active = sessionRef.current;
        if (!active || current.readOnly || active.projectId !== current.projectId
            || active.targetNodeId !== current.dialogNodeId
            || !current.nodesRef.current.some(node => node.id === active.targetNodeId)) return null;
        return active.targetNodeId;
    }, []);

    const toggle = useCallback((targetNodeId: string) => {
        if (activeTarget() === targetNodeId) {
            stop();
            return;
        }
        const current = optionsRef.current;
        const target = current.nodesRef.current.find(node => node.id === targetNodeId);
        if (current.readOnly || current.dialogNodeId !== targetNodeId || !canOpenCanvasNodePromptPanel(target)) return;
        current.cancelConnectionInteraction();
        const next = { projectId: current.projectId, targetNodeId };
        sessionRef.current = next;
        setSession(next);
    }, [activeTarget, stop]);

    const connectSource = useCallback((sourceNodeId: string) => {
        const targetNodeId = activeTarget();
        if (!targetNodeId) return false;
        const current = optionsRef.current;
        if (sourceNodeId === targetNodeId) {
            current.onFeedback({ kind: "warning", message: "目标节点不能引用自身" });
            return true;
        }
        const source = current.nodesRef.current.find(node => node.id === sourceNodeId);
        const restriction = source && source.type !== CanvasNodeType.Frame ? canvasReferenceSourceRestriction(source) : "";
        if (restriction) {
            current.onFeedback({ kind: "warning", message: restriction });
            return true;
        }
        const plan = planCanvasReferenceConnections({
            sourceNodeIds: [sourceNodeId], targetNodeId, nodes: current.nodesRef.current,
            connections: current.connectionsRef.current, config: current.config,
        });
        if (plan.connections.length) {
            // Advance the ref immediately so rapid repeated clicks see the same graph.
            const next = [...current.connectionsRef.current, ...plan.connections];
            current.connectionsRef.current = next;
            current.setConnections(next);
        }
        const added = plan.connected.length;
        const skipped = plan.skipped.length;
        if (skipped) {
            current.onFeedback({ kind: "warning", message: `${added ? `已添加 ${added} 个引用；` : ""}跳过 ${skipped} 个：${plan.skipped[0].reason}` });
        } else if (added) {
            current.onFeedback({ kind: "success", message: `已添加 ${added} 个引用${plan.duplicates.length ? `，${plan.duplicates.length} 个已引用` : ""}` });
        } else {
            current.onFeedback({ kind: "info", message: plan.duplicates.length ? "已引用这些素材，无需重复添加" : "此节点没有可引用的素材" });
        }
        return true;
    }, [activeTarget]);

    // Also used by the overview renderer, whose cards are painted on a canvas
    // and therefore have no DOM data-node-id for the capture listener below.
    const handleSourcePointerDown = useCallback((event: PointerInput, nodeId: string) => {
        // InfiniteCanvas owns Space + drag, including gestures starting on a card.
        if (event.button !== 0 || !activeTarget()
            || optionsRef.current.containerRef.current?.dataset.canvasSpacePressed === "true") return false;
        event.preventDefault();
        event.stopPropagation();
        connectSource(nodeId);
        return true;
    }, [activeTarget, connectSource]);

    const consumeEscape = useCallback(() => {
        if (!activeTarget()) return false;
        stop();
        return true;
    }, [activeTarget, stop]);

    useEffect(() => {
        if (!activeTargetNodeId) return;
        const container = options.containerRef.current;
        const sourceAt = (event: Event) => {
            const element = event.target instanceof Element ? event.target : null;
            if (!element || !container?.contains(element)
                || element.closest("[data-canvas-node-panel], [role='dialog'], [role='menu'], .ant-popover, .ant-dropdown")) return null;
            return element.closest<HTMLElement>("[data-node-id]")?.dataset.nodeId || null;
        };
        const pointerDown = (event: PointerEvent) => {
            const id = sourceAt(event);
            if (id) handleSourcePointerDown(event, id);
        };
        const click = (event: MouseEvent) => {
            const id = sourceAt(event);
            if (!id || event.button !== 0 || !activeTarget() || container?.dataset.canvasSpacePressed === "true") return;
            // Suppress play/rename/tool actions after the reference click.
            event.preventDefault();
            event.stopPropagation();
            if (event.type === "click" && event.detail === 0) connectSource(id);
        };
        const escape = (event: KeyboardEvent) => {
            if (event.key !== "Escape" || !consumeEscape()) return;
            event.preventDefault();
            event.stopImmediatePropagation();
        };
        container?.addEventListener("pointerdown", pointerDown, true);
        container?.addEventListener("click", click, true);
        container?.addEventListener("dblclick", click, true);
        window.addEventListener("keydown", escape, true);
        return () => {
            container?.removeEventListener("pointerdown", pointerDown, true);
            container?.removeEventListener("click", click, true);
            container?.removeEventListener("dblclick", click, true);
            window.removeEventListener("keydown", escape, true);
        };
    }, [activeTargetNodeId, activeTarget, connectSource, consumeEscape, handleSourcePointerDown, options.containerRef]);

    return { activeTargetNodeId, toggle, stop, consumeEscape, handleSourcePointerDown };
}
