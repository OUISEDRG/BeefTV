import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type Dispatch, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent, type SetStateAction } from "react";
import { App } from "antd";
import { nanoid } from "nanoid";
import { latchCanvasConnectionApproach, type CanvasConnectionApproach } from "@/lib/canvas/canvas-connection-tilt";

import type { PendingConnectionCreate } from "@/components/canvas/canvas-workspace-overlays";
import type { CanvasConnectionFeedback } from "@/components/canvas/canvas-connection-feedback";
import { getNodeSpec } from "@/constant/canvas";
import { batchSourceRestriction, buildBatchConnectionCreateRequest, type CanvasBatchConnectionPreview } from "@/lib/canvas/canvas-batch-connection";
import { planCanvasReferenceConnections, resolveCanvasGroupReferenceSources } from "@/lib/canvas/canvas-group-references";
import { isFrameNode } from "@/lib/canvas/canvas-frame";
import { resolveCanvasConnectionDropTarget, type CanvasConnectionDropTarget } from "@/lib/canvas/canvas-connection-drop-target";
import { sortCanvasNodesByStackOrder, type CanvasNodeStackOrder } from "@/lib/canvas/canvas-node-stack-order";
import { connectedNodeCenterFromEdgeDrop } from "@/lib/canvas/canvas-connected-node-placement";
import { canvasConnectionError } from "@/lib/canvas/canvas-connection-policy";
import { attachNodeToStoryboardRow, createCanvasNode, normalizeConnection, storyboardPromptTemplateMetadata, storyboardRowFromHandle } from "@/lib/canvas/canvas-project-domain";
import { createCanvasDrawingFromImage } from "@/lib/canvas/canvas-drawing-storage";
import type { CanvasDrawingEngine } from "@/lib/canvas/canvas-drawing-engine";
import { preferredMediaGeneratorMetadata } from "@/lib/canvas/canvas-generation-config";
import { canOpenCanvasNodePromptPanel } from "@/lib/canvas/canvas-node-semantics";
import { normalizeRunningHubCapability, type AiConfig } from "@/stores/use-config-store";
import { CanvasNodeType, type CanvasConnection, type CanvasNodeData, type CanvasNodeMetadata, type ConnectionHandle, type Position, type ViewportTransform } from "@/types/canvas";
import { workflowProviderPluginEnabled } from "@/lib/plugins/builtin/workflows";
import { usePluginStore } from "@/stores/use-plugin-store";
import type { CanvasPageUiStoreHook } from "@/stores/canvas/use-canvas-page-ui-store";
import { canvasConnectionPointerTarget, canvasReferenceChipAtPoint } from "./canvas-connection-pointer-target";

type UseCanvasConnectionControllerOptions = {
    pageUi: CanvasPageUiStoreHook;
    projectId: string;
    config: AiConfig;
    defaultDrawingEngine: CanvasDrawingEngine;
    nodesRef: { current: CanvasNodeData[] };
    nodeStackOrder: CanvasNodeStackOrder;
    connectionsRef: { current: CanvasConnection[] };
    viewportRef: { current: ViewportTransform };
    scriptScrollTopById: Record<string, number>;
    screenToCanvas: (clientX: number, clientY: number) => Position;
    setNodes: Dispatch<SetStateAction<CanvasNodeData[]>>;
    setConnections: Dispatch<SetStateAction<CanvasConnection[]>>;
    setDrawingNodeId: Dispatch<SetStateAction<string | null>>;
    onConnectedNodeCreated?: (node: CanvasNodeData, sourceNodeId?: string) => void;
    onReplaceReference?: (targetNodeId: string, oldReference: { id: string; nodeId?: string; label?: string; title?: string }, sourceNodeId: string) => void;
    onConnectionInteractionStart?: () => void;
    onConnectionFeedback?: (feedback: CanvasConnectionFeedback) => void;
};

export type ConnectionReplaceHover = {
    targetNodeId: string;
    referenceLabel: string;
    clientX: number;
    clientY: number;
};

const NODE_STATUS_IDLE = "idle" as const;

/**
 * Keep quick-created nodes on the side of their real source and avoid
 * stacking repeated outputs on top of one another. Positions are world-space
 * centers, while node data stores top-left coordinates.
 */
function placeConnectedNodeWithoutOverlap(
    nodes: CanvasNodeData[],
    source: CanvasNodeData | undefined,
    handleType: ConnectionHandle["handleType"],
    anchorY: number,
    size: { width: number; height: number },
): Position {
    if (!source) return { x: 0, y: anchorY };

    const gap = 36;
    const left = handleType === "source"
        ? source.position.x + source.width + 96
        : source.position.x - 96 - size.width;
    let top = anchorY - size.height / 2;

    // Resolve vertical collisions deterministically. Repeated outputs are
    // stacked downward, preserving the source-side alignment and leaving a
    // small LibTV-like breathing gap between cards.
    for (let pass = 0; pass < nodes.length + 1; pass += 1) {
        let moved = false;
        for (const node of nodes) {
            if (node.id === source.id) continue;
            const overlapsX = left < node.position.x + node.width + gap
                && left + size.width + gap > node.position.x;
            const overlapsY = top < node.position.y + node.height + gap
                && top + size.height + gap > node.position.y;
            if (overlapsX && overlapsY) {
                top = node.position.y + node.height + gap;
                moved = true;
            }
        }
        if (!moved) break;
    }

    return { x: left + size.width / 2, y: top + size.height / 2 };
}

function selectRunningHubWorkflow(config: AiConfig) {
    const capability = normalizeRunningHubCapability(config.runningHub.capability);
    return config.runningHub.workflows.find((item) => item.workflowId.trim() === config.runningHub.workflowId.trim()
        && (item.kind === "app" ? "app" : "workflow") === config.runningHub.selectedKind)
        || config.runningHub.workflows.find((item) => normalizeRunningHubCapability(item.capability, capability) === capability)
        || config.runningHub.workflows[0];
}

export function useCanvasConnectionController({
    pageUi,
    projectId,
    config,
    defaultDrawingEngine,
    nodesRef,
    nodeStackOrder,
    connectionsRef,
    viewportRef,
    scriptScrollTopById,
    screenToCanvas,
    setNodes,
    setConnections,
    setDrawingNodeId,
    onConnectedNodeCreated,
    onReplaceReference,
    onConnectionInteractionStart,
    onConnectionFeedback,
}: UseCanvasConnectionControllerOptions) {
    const { message: legacyMessage } = App.useApp();
    const message = useMemo(() => {
        const report = (kind: "success" | "warning" | "error", text: string) => {
            if (onConnectionFeedback) onConnectionFeedback({ kind: kind === "error" ? "warning" : kind, message: text });
            else legacyMessage[kind](text);
        };
        return { warning: (text: string) => report("warning", text), success: (text: string) => report("success", text), error: (text: string) => report("error", text) };
    }, [legacyMessage, onConnectionFeedback]);
    const runtimeStatuses = usePluginStore((state) => state.runtimeStatuses);
    const [connectingParams, setConnectingParams] = useState<ConnectionHandle | null>(null);
    const [connectionTargetNodeId, setConnectionTargetNodeId] = useState<string | null>(null);
    const [connectionApproach, setConnectionApproach] = useState<CanvasConnectionApproach>(null);
    const [connectionTargetAnchorRatio, setConnectionTargetAnchorRatio] = useState<number | undefined>();
    const [connectionReplaceHover, setConnectionReplaceHover] = useState<ConnectionReplaceHover | null>(null);
    const [pendingConnectionCreate, setPendingConnectionCreate] = useState<PendingConnectionCreate | null>(null);
    const [batchConnectionPreview, setBatchConnectionPreview] = useState<CanvasBatchConnectionPreview | null>(null);
    const [mouseWorld, setMouseWorld] = useState<Position>({ x: 0, y: 0 });
    const connectingParamsRef = useRef(connectingParams);
    const connectingPointerIdRef = useRef<number | null>(null);
    const connectingPointerStartRef = useRef<Position | null>(null);
    const pendingConnectionCreateRef = useRef(pendingConnectionCreate);
    const batchConnectionPreviewRef = useRef<CanvasBatchConnectionPreview | null>(null);
    const batchConnectionPointerIdRef = useRef<number | null>(null);
    const batchConnectionPointerStartRef = useRef<Position | null>(null);
    const batchGroupSourceIdRef = useRef<string | undefined>(undefined);
    const pointerMoveFrameRef = useRef<number | null>(null);
    const latestPointerMoveRef = useRef<PointerEvent | null>(null);
    const hoveredReplaceElRef = useRef<HTMLElement | null>(null);

    const updateConnectionReplaceHover = useCallback((element: HTMLElement | null, clientX = 0, clientY = 0) => {
        if (hoveredReplaceElRef.current === element) {
            if (element) {
                setConnectionReplaceHover((prev) => prev ? { ...prev, clientX, clientY } : null);
            }
            return;
        }
        if (hoveredReplaceElRef.current) {
            hoveredReplaceElRef.current.removeAttribute("data-connection-hover-replace");
        }
        hoveredReplaceElRef.current = element;
        if (element) {
            element.setAttribute("data-connection-hover-replace", "true");
            const targetNodeId = element.getAttribute("data-target-node-id") || "";
            const referenceLabel = element.getAttribute("data-reference-label") || "参考图";
            setConnectionReplaceHover({ targetNodeId, referenceLabel, clientX, clientY });
        } else {
            setConnectionReplaceHover(null);
        }
    }, []);

    useLayoutEffect(() => {
        connectingParamsRef.current = connectingParams;
        pendingConnectionCreateRef.current = pendingConnectionCreate;
    }, [connectingParams, pendingConnectionCreate]);

    const updateBatchConnectionPreview = useCallback((next: CanvasBatchConnectionPreview | null) => {
        batchConnectionPreviewRef.current = next;
        setBatchConnectionPreview(next);
        setConnectionApproach((previous) => latchCanvasConnectionApproach(previous, next && next.status !== "invalid" ? next.targetNodeId : null, next?.mouseWorld || { x: 0, y: 0 }));
    }, []);

    const clearBatchConnection = useCallback(() => {
        batchGroupSourceIdRef.current = undefined;
        batchConnectionPointerIdRef.current = null;
        batchConnectionPointerStartRef.current = null;
        updateBatchConnectionPreview(null);
    }, [updateBatchConnectionPreview]);

    const setConnecting = useCallback((next: ConnectionHandle | null) => {
        connectingParamsRef.current = next;
        setConnectingParams(next);
        if (!next) {
            updateConnectionReplaceHover(null);
            setConnectionApproach(null);
            connectingPointerIdRef.current = null;
            connectingPointerStartRef.current = null;
            setConnectionTargetNodeId(null);
            setConnectionTargetAnchorRatio(undefined);
        }
    }, [updateConnectionReplaceHover]);

    const closeConnectionCreateMenu = useCallback(() => {
        pendingConnectionCreateRef.current = null;
        setPendingConnectionCreate(null);
    }, []);

    const cancelPendingConnectionCreate = useCallback(() => {
        closeConnectionCreateMenu();
        setConnecting(null);
        clearBatchConnection();
    }, [clearBatchConnection, closeConnectionCreateMenu, setConnecting]);

    const previewBatchConnection = useCallback((sourceNodeIds: string[], targetNodeId: string | null, targetHandleId: string | undefined, targetAnchorRatio: number | undefined, mouseWorld: Position) => {
        const plan = targetNodeId
            ? planCanvasReferenceConnections({ sourceNodeIds: batchGroupSourceIdRef.current ? [batchGroupSourceIdRef.current] : sourceNodeIds, targetNodeId, targetHandleId, targetAnchorRatio, nodes: nodesRef.current, connections: connectionsRef.current, config })
            : null;
        const eligibleSourceCount = sourceNodeIds.filter((id) => {
            const node = nodesRef.current.find((item) => item.id === id);
            return Boolean(node && !batchSourceRestriction(node));
        }).length;
        const status = !targetNodeId || !plan ? "idle" : plan.connections.length === eligibleSourceCount ? "valid" : plan.connections.length ? "partial" : "invalid";
        updateBatchConnectionPreview({ sourceNodeIds, targetNodeId, targetHandleId, targetAnchorRatio, mouseWorld, status });
        return plan;
    }, [config, connectionsRef, nodesRef, updateBatchConnectionPreview]);

    const commitBatchConnection = useCallback((sourceNodeIds: string[], targetNodeId: string, targetHandleId?: string, targetAnchorRatio?: number, groupSourceNodeId = batchGroupSourceIdRef.current) => {
        const plan = planCanvasReferenceConnections({ sourceNodeIds: groupSourceNodeId ? [groupSourceNodeId] : sourceNodeIds, targetNodeId, targetHandleId, targetAnchorRatio, nodes: nodesRef.current, connections: connectionsRef.current, config });
        if (!plan.connections.length) {
            const reason = plan.skipped[0]?.reason || (plan.duplicates.length ? "这些素材已连接到目标节点" : "没有可建立的连接");
            message.warning(reason);
            return plan;
        }
        setNodes((currentNodes) => plan.connections.reduce((current, connection) => attachNodeToStoryboardRow(current, connection), currentNodes));
        setConnections((currentConnections) => [...currentConnections, ...plan.connections]);
        pageUi.getState().setContextMenu(null);
        const skippedCount = plan.skipped.length;
        const duplicateCount = plan.duplicates.length;
        const suffix = skippedCount || duplicateCount ? `，跳过 ${skippedCount + duplicateCount} 个` : "";
        if (skippedCount) message.warning(`已连接 ${plan.connected.length} 个节点${suffix}：${plan.skipped[0].reason}`);
        else message.success(`已连接 ${plan.connected.length} 个节点${suffix}`);
        return plan;
    }, [pageUi, projectId, config, connectionsRef, message, nodesRef, setConnections, setNodes]);

    const connectNodes = useCallback((current: ConnectionHandle, targetNodeId: string, targetHandleId?: string, targetAnchorRatio?: number) => {
        if (current.nodeId === targetNodeId) return;
        const targetNode = nodesRef.current.find((node) => node.id === targetNodeId);
        if (isFrameNode(targetNode)) {
            if (current.handleType === "target") commitBatchConnection([], current.nodeId, current.handleId, current.anchorRatio, targetNodeId);
            else message.warning("组不能作为引用目标，请连接到组内具体节点");
            return;
        }
        const connection = normalizeConnection(current.nodeId, targetNodeId, nodesRef.current, current.handleType);
        if (!connection) {
            message.warning("配置节点之间不能连接");
            return;
        }
        const { fromNodeId, toNodeId } = connection;
        const fromHandleId = fromNodeId === current.nodeId ? current.handleId : targetHandleId;
        const toHandleId = toNodeId === current.nodeId ? current.handleId : targetHandleId;
        const fromAnchorRatio = fromNodeId === current.nodeId ? current.anchorRatio : targetAnchorRatio;
        const toAnchorRatio = toNodeId === current.nodeId ? current.anchorRatio : targetAnchorRatio;
        const policyError = canvasConnectionError(config, nodesRef.current, connectionsRef.current, { fromNodeId, toNodeId });
        if (policyError) {
            message.warning(policyError);
            return;
        }
        const exists = connectionsRef.current.find((item) => item.fromNodeId === fromNodeId && item.toNodeId === toNodeId && item.fromHandleId === fromHandleId && item.toHandleId === toHandleId);
        if (exists) {
            setConnections((currentConnections) => currentConnections.map((item) => item.id === exists.id ? { ...item, fromAnchorRatio, toAnchorRatio } : item));
        } else {
            setConnections((currentConnections) => [...currentConnections, { id: `conn-${Date.now()}`, fromNodeId, toNodeId, fromHandleId, toHandleId, fromAnchorRatio, toAnchorRatio }]);
            setNodes((currentNodes) => attachNodeToStoryboardRow(currentNodes, { fromNodeId, toNodeId, fromHandleId, toHandleId }));
        }
        pageUi.getState().setContextMenu(null);
    }, [pageUi, projectId, commitBatchConnection, config, connectionsRef, message, nodesRef, setConnections, setNodes]);

    const createConnectedNode = useCallback(async (type: CanvasNodeType.Image | CanvasNodeType.Text | CanvasNodeType.Script | CanvasNodeType.BatchTable | CanvasNodeType.Video | CanvasNodeType.Audio | CanvasNodeType.Drawing | CanvasNodeType.Config | CanvasNodeType.MediaConversion, pending: PendingConnectionCreate, workflowProvider?: "runninghub") => {
        const nodeType = type;
        const batchSourceNodeIds = pending.groupSourceNodeId
            ? resolveCanvasGroupReferenceSources(pending.groupSourceNodeId, nodesRef.current).sourceNodeIds
            : pending.batchSourceNodeIds?.length ? Array.from(new Set(pending.batchSourceNodeIds)) : [];
        if (pending.groupSourceNodeId && !batchSourceNodeIds.length) {
            message.warning("组内没有可引用的素材");
            cancelPendingConnectionCreate();
            return;
        }
        const batchSourceNodes = batchSourceNodeIds
            .map((nodeId) => nodesRef.current.find((node) => node.id === nodeId))
            .filter((node): node is CanvasNodeData => Boolean(node));
        const storyboardRow = batchSourceNodeIds.length ? undefined : nodeType === CanvasNodeType.Video ? storyboardRowFromHandle(nodesRef.current, pending.connection.nodeId, pending.connection.handleId) : undefined;
        const videoPrompt = storyboardRow ? (storyboardRow.videoMotionPrompt || storyboardRow.plotDescription).trim() : "";
        const sourceNode = pending.connection.handleType === "source" ? nodesRef.current.find((node) => node.id === pending.connection.nodeId) : undefined;
        const batchScriptPrompt = batchSourceNodes
            .filter((node) => node.type === CanvasNodeType.Text)
            .map((node) => (node.metadata?.content || node.metadata?.prompt || "").trim())
            .filter(Boolean)
            .join("\n\n");
        const scriptPrompt = nodeType === CanvasNodeType.Script
            ? batchSourceNodeIds.length ? batchScriptPrompt : sourceNode?.type === CanvasNodeType.Text ? (sourceNode.metadata?.content || sourceNode.metadata?.prompt || "").trim() : ""
            : "";
        const selectedWorkflowProvider = nodeType === CanvasNodeType.Config
            ? workflowProvider || (workflowProviderPluginEnabled(runtimeStatuses, "runninghub") ? "runninghub" : undefined)
            : undefined;
        if (selectedWorkflowProvider && !workflowProviderPluginEnabled(runtimeStatuses, selectedWorkflowProvider)) {
            message.error("RunningHub 工作流插件未启用");
            closeConnectionCreateMenu();
            setConnecting(null);
            return;
        }
        const runningHubWorkflow = selectedWorkflowProvider === "runninghub" ? selectRunningHubWorkflow(config) : undefined;
        const workflowCapability = normalizeRunningHubCapability(runningHubWorkflow?.capability, normalizeRunningHubCapability(config.runningHub.capability));
        const metadata: CanvasNodeMetadata | undefined = nodeType === CanvasNodeType.Config
            ? {
                generationMode: selectedWorkflowProvider ? workflowCapability === "video" ? "video" as const : workflowCapability === "audio" ? "audio" as const : "image" as const : "image" as const,
                workflowProvider: selectedWorkflowProvider || "model",
                status: NODE_STATUS_IDLE,
                ...(selectedWorkflowProvider === "runninghub" ? {
                    workflowTitle: "RunningHub 工作流",
                    ...(runningHubWorkflow ? { runningHubWorkflowId: runningHubWorkflow.workflowId, runningHubWorkflowKind: runningHubWorkflow.kind === "app" ? "app" as const : "workflow" as const } : {}),
                } : {})
              }
            : nodeType === CanvasNodeType.Drawing
            ? { drawingEngine: defaultDrawingEngine }
            : nodeType === CanvasNodeType.Script && scriptPrompt
              ? { prompt: scriptPrompt, composerContent: scriptPrompt }
            : nodeType === CanvasNodeType.Video && storyboardRow
              ? preferredMediaGeneratorMetadata(config, "video", { prompt: videoPrompt, composerContent: videoPrompt, ...storyboardPromptTemplateMetadata(storyboardRow, "video"), generationMode: "video" as const, videoEditOperation: "text_to_video" as const, workflowKind: "shot" as const, workflowTitle: `镜头 ${storyboardRow.shotNumber} 视频`, shotIndex: storyboardRow.shotNumber, seconds: String(storyboardRow.durationSeconds), status: NODE_STATUS_IDLE })
            : nodeType === CanvasNodeType.Image || nodeType === CanvasNodeType.Video || nodeType === CanvasNodeType.Audio
              ? preferredMediaGeneratorMetadata(config, nodeType === CanvasNodeType.Image ? "image" : nodeType === CanvasNodeType.Video ? "video" : "audio")
              : undefined;
        // A pin click has no dragged endpoint, so place its new node beside
        // the source. A dragged connection must use its recorded drop point.
        const sourceNodeForQuickCreate = nodesRef.current.find((node) => node.id === pending.connection.nodeId);
        const spec = getNodeSpec(nodeType);
        const anchorY = sourceNodeForQuickCreate ? sourceNodeForQuickCreate.position.y + sourceNodeForQuickCreate.height * (pending.connection.anchorRatio ?? 0.5) : pending.position.y;
        const position = pending.quick && sourceNodeForQuickCreate
            ? placeConnectedNodeWithoutOverlap(
                nodesRef.current,
                sourceNodeForQuickCreate,
                pending.connection.handleType,
                anchorY,
                spec,
            )
            : batchSourceNodeIds.length
              ? pending.position
              : connectedNodeCenterFromEdgeDrop(pending.position, spec, pending.connection.handleType);
        const newNode = createCanvasNode(nodeType, position, metadata);
        if (nodeType === CanvasNodeType.Config && selectedWorkflowProvider) newNode.title = "RunningHub 工作流";
        if (storyboardRow) newNode.title = `镜头 ${storyboardRow.shotNumber} · 视频`;
        if (batchSourceNodeIds.length && nodeType === CanvasNodeType.Drawing) {
            message.error("批量连接暂不支持创建绘图，请先连接到普通节点");
            closeConnectionCreateMenu();
            setConnecting(null);
            return;
        }
        const batchPlan = batchSourceNodeIds.length
            ? planCanvasReferenceConnections({ sourceNodeIds: pending.groupSourceNodeId ? [pending.groupSourceNodeId] : batchSourceNodeIds, targetNodeId: newNode.id, nodes: [...nodesRef.current, newNode], connections: connectionsRef.current, config, allowCapacityOverflow: !pending.groupSourceNodeId })
            : null;
        if (batchPlan) {
            if (!batchPlan.connections.length) {
                const detail = batchPlan.skipped.slice(0, 3).map((item) => item.reason).join("；");
                message.warning(detail ? `没有可建立的连接：${detail}` : "没有可建立的连接");
                closeConnectionCreateMenu();
                setConnecting(null);
                return;
            }
            const nextConnections = [...connectionsRef.current, ...batchPlan.connections];
            const nextNodes = batchPlan.connections.reduce((currentNodes, connection) => attachNodeToStoryboardRow(currentNodes, connection), [...nodesRef.current, newNode]);
            onConnectedNodeCreated?.(newNode, pending.connection.nodeId);
            nodesRef.current = nextNodes;
            connectionsRef.current = nextConnections;
            setNodes(nextNodes);
            setConnections(nextConnections);
            pageUi.getState().setSelectedNodeIds(new Set([newNode.id]));
            pageUi.getState().setSelectedConnectionId(null);
            pageUi.getState().setDialogNodeId(nodeType !== CanvasNodeType.Text && nodeType !== CanvasNodeType.BatchTable && nodeType !== CanvasNodeType.MediaConversion && canOpenCanvasNodePromptPanel(newNode) ? newNode.id : null);
            const skippedCount = batchPlan.skipped.length;
            const duplicateCount = batchPlan.duplicates.length;
            const suffix = skippedCount || duplicateCount ? `，跳过 ${skippedCount + duplicateCount} 个` : "";
            if (skippedCount) message.warning(`已创建并连接 ${batchPlan.connections.length} 个源节点${suffix}：${batchPlan.skipped[0].reason}`);
            else message.success(`已创建节点并连接 ${batchPlan.connections.length} 个源节点${suffix}`);
            closeConnectionCreateMenu();
            setConnecting(null);
            return;
        }
        const connection = normalizeConnection(pending.connection.nodeId, newNode.id, [...nodesRef.current, newNode], pending.connection.handleType);
        if (!connection) {
            message.warning("当前节点不能建立这条连线");
            closeConnectionCreateMenu();
            setConnecting(null);
            return;
        }
        const policyError = canvasConnectionError(config, [...nodesRef.current, newNode], connectionsRef.current, connection);
        if (policyError) {
            message.warning(policyError);
            closeConnectionCreateMenu();
            setConnecting(null);
            return;
        }
        if (nodeType === CanvasNodeType.Drawing) {
            const drawingSourceNode = nodesRef.current.find((node) => node.id === pending.connection.nodeId);
            const sourceUrl = drawingSourceNode?.type === CanvasNodeType.Image ? drawingSourceNode.metadata?.content : "";
            if (pending.connection.handleType !== "source" || !drawingSourceNode || !sourceUrl || !newNode.metadata?.drawingId) {
                message.error("只有已有图片内容的输出连线可以创建绘图");
                closeConnectionCreateMenu();
                setConnecting(null);
                return;
            }
            closeConnectionCreateMenu();
            setConnecting(null);
            try {
                const saved = await createCanvasDrawingFromImage(projectId, newNode.metadata.drawingId, defaultDrawingEngine, {
                    url: sourceUrl,
                    storageKey: drawingSourceNode.metadata?.storageKey,
                    name: drawingSourceNode.title || "来源图片",
                    mimeType: drawingSourceNode.metadata?.mimeType,
                });
                newNode.title = `${drawingSourceNode.title || "图片"} · 绘图`;
                newNode.metadata = {
                    ...newNode.metadata,
                    drawingEngine: saved.engine,
                    drawingRevision: saved.revision,
                    drawingUpdatedAt: saved.updatedAt,
                    drawingShapeCount: saved.shapeCount,
                    drawingPageCount: saved.pageCount,
                };
            } catch (error) {
                message.error(error instanceof Error ? `创建绘图失败：${error.message}` : "创建绘图失败");
                return;
            }
        }
        const fromHandleId = connection.fromNodeId === pending.connection.nodeId ? pending.connection.handleId : undefined;
        const toHandleId = connection.toNodeId === pending.connection.nodeId ? pending.connection.handleId : undefined;
        const fromAnchorRatio = connection.fromNodeId === pending.connection.nodeId ? pending.connection.anchorRatio : 0.5;
        const toAnchorRatio = connection.toNodeId === pending.connection.nodeId ? pending.connection.anchorRatio : 0.5;
        const connected = { ...connection, fromHandleId, toHandleId, fromAnchorRatio, toAnchorRatio };
        onConnectedNodeCreated?.(newNode, pending.connection.nodeId);
        setNodes((currentNodes) => attachNodeToStoryboardRow([...currentNodes, newNode], connected));
        setConnections((currentConnections) => [...currentConnections, { id: nanoid(), ...connected }]);
        pageUi.getState().setSelectedNodeIds(new Set([newNode.id]));
        pageUi.getState().setSelectedConnectionId(null);
        if (nodeType === CanvasNodeType.Drawing) setDrawingNodeId(newNode.id);
        pageUi.getState().setDialogNodeId(nodeType !== CanvasNodeType.Text && nodeType !== CanvasNodeType.BatchTable && nodeType !== CanvasNodeType.MediaConversion && canOpenCanvasNodePromptPanel(newNode) ? newNode.id : null);
        closeConnectionCreateMenu();
        setConnecting(null);
    }, [cancelPendingConnectionCreate, closeConnectionCreateMenu, config, connectionsRef, defaultDrawingEngine, message, nodesRef, onConnectedNodeCreated, projectId, runtimeStatuses, setConnecting, setConnections, setDrawingNodeId, setNodes]);

    const getConnectionCreateDisabledReason = useCallback((type: CanvasNodeType.Image | CanvasNodeType.Text | CanvasNodeType.Script | CanvasNodeType.BatchTable | CanvasNodeType.Video | CanvasNodeType.Audio | CanvasNodeType.Drawing | CanvasNodeType.Config | CanvasNodeType.MediaConversion, pending: PendingConnectionCreate, workflowProvider?: "runninghub") => {
        const nodeType = type;
        if (nodeType === CanvasNodeType.Config) {
            if (workflowProvider && !workflowProviderPluginEnabled(runtimeStatuses, workflowProvider)) return "RunningHub 工作流插件未启用";
        }
        if (pending.groupSourceNodeId || pending.batchSourceNodeIds?.length) {
            if (nodeType === CanvasNodeType.Drawing || nodeType === CanvasNodeType.Config) return "批量连接暂不支持此节点类型";
            const pendingNode: CanvasNodeData = { id: "__pending-connection-node__", type: nodeType, title: "", position: pending.position, width: getNodeSpec(nodeType).width, height: getNodeSpec(nodeType).height };
            const plan = planCanvasReferenceConnections({ sourceNodeIds: pending.groupSourceNodeId ? [pending.groupSourceNodeId] : pending.batchSourceNodeIds!, targetNodeId: pendingNode.id, nodes: [...nodesRef.current, pendingNode], connections: connectionsRef.current, config, allowCapacityOverflow: !pending.groupSourceNodeId });
            return plan.connections.length ? "" : plan.skipped[0]?.reason || "当前选中的节点不能连接到此类型";
        }
        const spec = getNodeSpec(nodeType);
        const pendingNode: CanvasNodeData = { id: "__pending-connection-node__", type: nodeType, title: "", position: pending.position, width: spec.width, height: spec.height };
        const pendingNodes = [...nodesRef.current, pendingNode];
        const connection = normalizeConnection(pending.connection.nodeId, pendingNode.id, pendingNodes, pending.connection.handleType);
        if (!connection) return "当前节点类型不能这样连接";
        return canvasConnectionError(config, pendingNodes, connectionsRef.current, connection);
    }, [config, connectionsRef, nodesRef, runtimeStatuses]);

    const resolveDropTarget = useCallback((clientX: number, clientY: number, current: ConnectionHandle, getError: (node: CanvasNodeData, handleId?: string) => string) => resolveCanvasConnectionDropTarget({
        nodes: sortCanvasNodesByStackOrder(nodesRef.current, nodeStackOrder),
        point: screenToCanvas(clientX, clientY), scale: viewportRef.current.k, current, scriptScrollTopById,
        ...canvasConnectionPointerTarget(clientX, clientY), getError,
    }), [nodesRef, nodeStackOrder, screenToCanvas, scriptScrollTopById, viewportRef]);

    const getConnectionDropTarget = useCallback((clientX: number, clientY: number, current: ConnectionHandle): CanvasConnectionDropTarget => resolveDropTarget(clientX, clientY, current, (node) => {
        if (node.id === current.nodeId) return "目标节点不能连接到自身";
        if (isFrameNode(node)) {
            if (current.handleType !== "target") return "组不能作为引用目标，请连接到组内具体节点";
            const plan = planCanvasReferenceConnections({ sourceNodeIds: [node.id], targetNodeId: current.nodeId, targetHandleId: current.handleId, targetAnchorRatio: current.anchorRatio, nodes: nodesRef.current, connections: connectionsRef.current, config });
            return plan.connections.length || plan.duplicates.length ? "" : plan.skipped[0]?.reason || "组内没有可引用的素材";
        }
        const connection = normalizeConnection(current.nodeId, node.id, nodesRef.current, current.handleType);
        return connection ? canvasConnectionError(config, nodesRef.current, connectionsRef.current, connection) : "这两个节点不能连接";
    }), [config, connectionsRef, nodesRef, resolveDropTarget]);

    const getBatchConnectionDropTarget = useCallback((clientX: number, clientY: number, sourceNodeIds: string[]): CanvasConnectionDropTarget => {
        const source = sourceNodeIds
            .map((id) => nodesRef.current.find((node) => node.id === id))
            .find((node): node is CanvasNodeData => Boolean(node && !batchSourceRestriction(node)));
        if (!source) return { nodeId: null, isNearNode: false };

        return resolveDropTarget(clientX, clientY, { nodeId: source.id, handleType: "source" }, (node, handleId) => {
            if (isFrameNode(node)) return "组不能作为引用目标，请连接到组内具体节点";
            const plan = planCanvasReferenceConnections({ sourceNodeIds: batchGroupSourceIdRef.current ? [batchGroupSourceIdRef.current] : sourceNodeIds, targetNodeId: node.id, targetHandleId: handleId, nodes: nodesRef.current, connections: connectionsRef.current, config });
            return plan.connections.length || plan.duplicates.length ? "" : plan.skipped[0]?.reason || "没有可建立的连接";
        });
    }, [config, connectionsRef, nodesRef, resolveDropTarget]);

    const startBatchConnection = useCallback((event: ReactPointerEvent, sourceNodeIds: string[], groupSourceNodeId?: string) => {
        onConnectionInteractionStart?.();
        cancelPendingConnectionCreate();
        event.preventDefault();
        event.stopPropagation();
        const eligible = sourceNodeIds.filter((id) => {
            const node = nodesRef.current.find((item) => item.id === id);
            return Boolean(node && !batchSourceRestriction(node));
        });
        if (!eligible.length) {
            message.warning(groupSourceNodeId ? "组内没有可引用的素材" : "当前选区没有可作为连接源的节点");
            return;
        }
        batchGroupSourceIdRef.current = groupSourceNodeId;
        batchConnectionPointerIdRef.current = event.pointerId;
        batchConnectionPointerStartRef.current = { x: event.clientX, y: event.clientY };
        pageUi.getState().setSelectedConnectionId(null);
        const mouseWorld = screenToCanvas(event.clientX, event.clientY);
        previewBatchConnection(eligible, null, undefined, undefined, mouseWorld);
    }, [pageUi, projectId, cancelPendingConnectionCreate, message, nodesRef, onConnectionInteractionStart, previewBatchConnection, screenToCanvas]);

    const beginBatchConnectionMode = useCallback((sourceNodeIds: string[]) => {
        onConnectionInteractionStart?.();
        cancelPendingConnectionCreate();
        const eligible = sourceNodeIds.filter((id) => {
            const node = nodesRef.current.find((item) => item.id === id);
            return Boolean(node && !batchSourceRestriction(node));
        });
        const source = eligible.map((id) => nodesRef.current.find((node) => node.id === id)).find((node): node is CanvasNodeData => Boolean(node));
        if (!source) {
            message.warning("当前选区没有可作为连接源的节点");
            return;
        }
        previewBatchConnection(eligible, null, undefined, undefined, { x: source.position.x + source.width, y: source.position.y + source.height / 2 });
        pageUi.getState().setSelectedConnectionId(null);
    }, [pageUi, projectId, cancelPendingConnectionCreate, message, nodesRef, onConnectionInteractionStart, previewBatchConnection]);

    const finishBatchConnection = useCallback((clientX: number, clientY: number) => {
        const batch = batchConnectionPreviewRef.current;
        if (!batch) return false;
        const target = getBatchConnectionDropTarget(clientX, clientY, batch.sourceNodeIds);
        if (!target.nodeId) return false;
        if (target.error) message.warning(target.error);
        else commitBatchConnection(batch.sourceNodeIds, target.nodeId, target.handleId, target.anchorRatio);
        clearBatchConnection();
        return true;
    }, [clearBatchConnection, commitBatchConnection, getBatchConnectionDropTarget, message]);

    const openBatchConnectionCreateMenu = useCallback((clientX: number, clientY: number) => {
        const batch = batchConnectionPreviewRef.current;
        if (!batch) return false;
        const position = screenToCanvas(clientX, clientY);
        const request = buildBatchConnectionCreateRequest(batch.sourceNodeIds, nodesRef.current, position);
        if (!request) {
            clearBatchConnection();
            message.warning("当前选区没有可作为连接源的节点");
            return false;
        }
        const pending: PendingConnectionCreate = { ...request, groupSourceNodeId: batchGroupSourceIdRef.current };
        pendingConnectionCreateRef.current = pending;
        setPendingConnectionCreate(pending);
        setMouseWorld(position);
        clearBatchConnection();
        return true;
    }, [clearBatchConnection, message, nodesRef, screenToCanvas]);

    const handleBatchConnectionTargetClick = useCallback((event: ReactPointerEvent | ReactMouseEvent) => {
        if (!batchConnectionPreviewRef.current) return false;
        const completed = finishBatchConnection(event.clientX, event.clientY);
        if (!completed) message.warning("请点击目标节点的输入端");
        return true;
    }, [finishBatchConnection, message]);

    const finishConnection = useCallback((clientX: number, clientY: number) => {
        updateConnectionReplaceHover(null);
        if (pendingConnectionCreateRef.current) return;
        const currentConnection = connectingParamsRef.current;
        if (!currentConnection) return;

        const refChip = currentConnection.handleType === "source" ? canvasReferenceChipAtPoint(clientX, clientY) : null;
        if (refChip) {
            const targetNodeId = refChip.getAttribute("data-target-node-id");
            const oldNodeId = refChip.getAttribute("data-reference-node-id");
            const label = refChip.getAttribute("data-reference-label") || "";
            const id = refChip.getAttribute("data-reference-id") || "";
            const title = refChip.getAttribute("data-reference-title") || label;
            if (targetNodeId && oldNodeId && targetNodeId !== currentConnection.nodeId && onReplaceReference) {
                if (oldNodeId !== currentConnection.nodeId) onReplaceReference(targetNodeId, { id, nodeId: oldNodeId, label, title }, currentConnection.nodeId);
                setConnecting(null);
                return;
            }
        }

        const dropTarget = getConnectionDropTarget(clientX, clientY, currentConnection);
        if (dropTarget.nodeId) {
            if (dropTarget.error) message.warning(dropTarget.error);
            else connectNodes(currentConnection, dropTarget.nodeId, dropTarget.handleId, dropTarget.anchorRatio);
            setConnecting(null);
        } else if (dropTarget.isNearNode) {
            setConnecting(null);
        } else {
            const position = screenToCanvas(clientX, clientY);
            setMouseWorld(position);
            const pending = { connection: currentConnection, position };
            pendingConnectionCreateRef.current = pending;
            setPendingConnectionCreate(pending);
        }
    }, [connectNodes, getConnectionDropTarget, message, onReplaceReference, screenToCanvas, setConnecting, updateConnectionReplaceHover]);

    const handleConnectStart = useCallback((event: ReactPointerEvent, nodeId: string, handleType: "source" | "target", handleId?: string, anchorRatio?: number) => {
        onConnectionInteractionStart?.();
        event.preventDefault();
        event.stopPropagation();
        const sourceNode = nodesRef.current.find((node) => node.id === nodeId);
        if (handleType === "source" && isFrameNode(sourceNode)) {
            const active = connectingParamsRef.current;
            if (active?.handleType === "target") {
                connectNodes(active, nodeId);
                setConnecting(null);
            } else startBatchConnection(event, resolveCanvasGroupReferenceSources(nodeId, nodesRef.current).sourceNodeIds, nodeId);
            return;
        }
        // A new pin interaction always starts a fresh session. Without this
        // reset, an old quick-create menu can coexist with the new draft line;
        // pointerup then sees the stale pending menu and silently aborts.
        if (pendingConnectionCreateRef.current) closeConnectionCreateMenu();
        if (batchConnectionPreviewRef.current && handleType === "target") {
            commitBatchConnection(batchConnectionPreviewRef.current.sourceNodeIds, nodeId, handleId, anchorRatio);
            clearBatchConnection();
            return;
        }
        // A batch table exposes real target pins inside its content. When a normal
        // image output is dragged onto one, commit that active connection instead
        // of starting a new connection session from the target pin.
        const activeConnection = connectingParamsRef.current;
        if (activeConnection && handleType === "target" && activeConnection.nodeId !== nodeId) {
            connectNodes(activeConnection, nodeId, handleId, anchorRatio);
            setConnecting(null);
            return;
        }
        if (activeConnection) setConnecting(null);
        if (batchConnectionPreviewRef.current) clearBatchConnection();
        connectingPointerIdRef.current = event.pointerId;
        connectingPointerStartRef.current = { x: event.clientX, y: event.clientY };
        setMouseWorld(screenToCanvas(event.clientX, event.clientY));
        setConnecting({ nodeId, handleType, handleId, anchorRatio });
        setConnectionTargetNodeId(null);
        setConnectionTargetAnchorRatio(undefined);
        pageUi.getState().setSelectedConnectionId(null);
    }, [pageUi, projectId, clearBatchConnection, closeConnectionCreateMenu, commitBatchConnection, connectNodes, nodesRef, onConnectionInteractionStart, screenToCanvas, setConnecting, startBatchConnection]);

    const handleConnectDrop = useCallback((event: ReactPointerEvent, nodeId: string, handleId?: string) => {
        event.preventDefault();
        event.stopPropagation();
        if (batchConnectionPreviewRef.current) {
            commitBatchConnection(batchConnectionPreviewRef.current.sourceNodeIds, nodeId, handleId);
            clearBatchConnection();
            return;
        }
        const current = connectingParamsRef.current;
        if (!current || current.nodeId === nodeId) return;
        connectNodes(current, nodeId, handleId);
        setConnecting(null);
    }, [clearBatchConnection, commitBatchConnection, connectNodes, setConnecting]);

    useEffect(() => {
        const cancelPendingPointerMove = () => {
            latestPointerMoveRef.current = null;
            if (pointerMoveFrameRef.current !== null) window.cancelAnimationFrame(pointerMoveFrameRef.current);
            pointerMoveFrameRef.current = null;
        };
        const flushPointerMove = () => {
            pointerMoveFrameRef.current = null;
            const event = latestPointerMoveRef.current;
            latestPointerMoveRef.current = null;
            if (!event) return;
            const batch = batchConnectionPreviewRef.current;
            if (batch && (batchConnectionPointerIdRef.current === null || batchConnectionPointerIdRef.current === event.pointerId)) {
                const target = getBatchConnectionDropTarget(event.clientX, event.clientY, batch.sourceNodeIds);
                const mouseWorld = screenToCanvas(event.clientX, event.clientY);
                previewBatchConnection(batch.sourceNodeIds, target.error ? null : target.nodeId, target.handleId, target.anchorRatio, mouseWorld);
                return;
            }
            const current = connectingParamsRef.current;
            if (!current || connectingPointerIdRef.current !== event.pointerId || pendingConnectionCreateRef.current) return;
            updateConnectionReplaceHover(current.handleType === "source" ? canvasReferenceChipAtPoint(event.clientX, event.clientY) : null, event.clientX, event.clientY);
            const dropTarget = getConnectionDropTarget(event.clientX, event.clientY, current);
            const point = screenToCanvas(event.clientX, event.clientY);
            const targetNodeId = dropTarget.error ? null : dropTarget.nodeId;
            setConnectionApproach((previous) => latchCanvasConnectionApproach(previous, targetNodeId, point));
            setConnectionTargetNodeId(targetNodeId);
            setConnectionTargetAnchorRatio(dropTarget.anchorRatio);
            setMouseWorld(point);
        };
        const handlePointerMove = (event: PointerEvent) => {
            // Pointer events can arrive faster than the canvas can paint. Keep
            // only the latest position and update the preview once per frame,
            // which prevents redundant React/Leafer work and visible jitter.
            latestPointerMoveRef.current = event;
            if (pointerMoveFrameRef.current === null) pointerMoveFrameRef.current = window.requestAnimationFrame(flushPointerMove);
        };
        const handlePointerUp = (event: PointerEvent) => {
            latestPointerMoveRef.current = null;
            if (pointerMoveFrameRef.current !== null) {
                window.cancelAnimationFrame(pointerMoveFrameRef.current);
                pointerMoveFrameRef.current = null;
            }
            if (batchConnectionPointerIdRef.current === event.pointerId) {
                const start = batchConnectionPointerStartRef.current;
                const batch = batchConnectionPreviewRef.current;
                if (batch && start && Math.hypot(event.clientX - start.x, event.clientY - start.y) <= 5) {
                    const target = getBatchConnectionDropTarget(event.clientX, event.clientY, batch.sourceNodeIds);
                    if (target.nodeId) {
                        if (target.error) message.warning(target.error);
                        else commitBatchConnection(batch.sourceNodeIds, target.nodeId, target.handleId, target.anchorRatio);
                        clearBatchConnection();
                    } else if (!target.isNearNode) openBatchConnectionCreateMenu(event.clientX, event.clientY);
                    else clearBatchConnection();
                    return;
                }
                const completed = finishBatchConnection(event.clientX, event.clientY);
                if (!completed) {
                    const target = getBatchConnectionDropTarget(event.clientX, event.clientY, batch?.sourceNodeIds || []);
                    if (!target.isNearNode) openBatchConnectionCreateMenu(event.clientX, event.clientY);
                    else clearBatchConnection();
                }
                return;
            }
            if (connectingPointerIdRef.current !== event.pointerId) return;
            const start = connectingPointerStartRef.current;
            if (start && Math.hypot(event.clientX - start.x, event.clientY - start.y) <= 5) {
                const current = connectingParamsRef.current;
                if (current) {
                    const pending = { connection: current, position: screenToCanvas(event.clientX, event.clientY), quick: true };
                    pendingConnectionCreateRef.current = pending;
                    setPendingConnectionCreate(pending);
                    setConnecting(null);
                    return;
                }
            }
            finishConnection(event.clientX, event.clientY);
        };
        const handlePointerCancel = (event: PointerEvent) => {
            cancelPendingPointerMove();
            updateConnectionReplaceHover(null);
            if (batchConnectionPointerIdRef.current === event.pointerId) {
                clearBatchConnection();
                return;
            }
            if (connectingPointerIdRef.current === event.pointerId) setConnecting(null);
        };
        const cancel = () => {
            cancelPendingPointerMove();
            updateConnectionReplaceHover(null);
            if (connectingParamsRef.current) setConnecting(null);
            if (batchConnectionPreviewRef.current) clearBatchConnection();
        };
        window.addEventListener("pointermove", handlePointerMove);
        window.addEventListener("pointerup", handlePointerUp);
        window.addEventListener("pointercancel", handlePointerCancel);
        window.addEventListener("blur", cancel);
        return () => {
            cancelPendingPointerMove();
            window.removeEventListener("pointermove", handlePointerMove);
            window.removeEventListener("pointerup", handlePointerUp);
            window.removeEventListener("pointercancel", handlePointerCancel);
            window.removeEventListener("blur", cancel);
        };
    }, [clearBatchConnection, commitBatchConnection, finishBatchConnection, finishConnection, getBatchConnectionDropTarget, getConnectionDropTarget, message, openBatchConnectionCreateMenu, previewBatchConnection, screenToCanvas, setConnecting, updateConnectionReplaceHover]);

    return {
        cancelPendingConnectionCreate,
        closeConnectionCreateMenu,
        connectionTargetNodeId,
        connectionApproach,
        connectionTargetAnchorRatio,
        connectionReplaceHover,
        connectingParams,
        createConnectedNode,
        getConnectionCreateDisabledReason,
        handleConnectStart,
        handleConnectDrop,
        handleBatchConnectionTargetClick,
        batchConnectionPreview,
        beginBatchConnectionMode,
        startBatchConnection,
        mouseWorld,
        pendingConnectionCreate,
        setConnecting,
    };
}
