import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from "react";
import type { useAppProps } from "antd/es/app/context";
import { nanoid } from "nanoid";

import type { CanvasImageCropRect } from "@/components/canvas/canvas-node-crop-dialog";
import type { CanvasImageMaskEditPayload } from "@/components/canvas/canvas-node-mask-edit-dialog";
import type { CanvasImageSplitParams } from "@/components/canvas/canvas-node-split-dialog";
import type { CanvasImageUpscaleParams } from "@/components/canvas/canvas-node-upscale-dialog";
import type { CanvasImageAngleParams } from "@/components/canvas/canvas-node-angle-dialog";
import { buildLightingLabel, type CanvasImageLightingOptions } from "@/components/canvas/canvas-node-lighting-dialog";
import type { CanvasImageEmotionPayload } from "@/components/canvas/canvas-node-emotion-panel";
import { NODE_DEFAULT_SIZE } from "@/constant/canvas";
import { cropDataUrl, splitDataUrl, upscaleDataUrl } from "@/lib/canvas/canvas-image-data";
import { isValidGridSplit, layoutGridSplitCells } from "@/lib/canvas/canvas-grid-split";
import { imageMetadata } from "@/lib/canvas/canvas-generation-task-sync";
import { findAvailableGenerationGroupPosition, imageGenerationChildPosition, imageGenerationGroupSize } from "@/lib/canvas/canvas-generation-layout";
import { canvasGenerationPromptMetadata } from "@/lib/canvas/canvas-generation-submission";
import { cancelIncompleteImageBatch, reconcileImageBatchRoot } from "@/lib/canvas/canvas-image-batch-retry";
import { buildAngleLabel, buildAnglePrompt, createCanvasNode } from "@/lib/canvas/canvas-project-domain";
import { resolveCanvasStyleExecution } from "@/lib/canvas/canvas-style-execution";
import { buildGenerationConfig, buildImageGenerationMetadata, nodeReferenceImage, isGenerationCanceled, runCanvasGenerationTaskToConsumer } from "@/lib/canvas/canvas-project-generation";
import { fitNodeSize } from "@/lib/canvas/canvas-node-size";
import { emotionProviderMask, normalizeEmotionPromptForProvider } from "@/lib/canvas/canvas-emotion";
import { emotionGenerationSettings, freezeImageEditSource, maskEditInputs, maskEditNeedsGuide } from "@/lib/canvas/canvas-local-image-edit";
import { DEFAULT_PORTRAIT_TEXTURE_SETTINGS } from "@/lib/canvas/canvas-portrait-texture";
import { IMAGE_PROMPT_REVERSE, VIDEO_PROMPT_REVERSE } from "@/lib/prompts";
import { createPortraitTextureNode } from "@/lib/canvas/canvas-image-source";
import { mediaResultMetadata } from "@/lib/canvas/canvas-node-semantics";
import { createImageToolGenerationNode } from "@/lib/canvas/image-tool-generation";
import { dreamaImageToolSupport } from "@/lib/canvas/dreama-image-tool-support";
import { canvasVideoReversePromptAvailable } from "@/lib/canvas/canvas-provider-capabilities";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { generationErrorMessage } from "@/lib/generation-error";
import { canvasGenerationFailureMetadata } from "./canvas-generation-failure";
import { defaultImageParamsForModel } from "@/lib/model-selection";
import { navigateToSettings } from "@/lib/settings-navigation";
import { resolveImageUrl, uploadImage } from "@/services/image-storage";
import type { GenerationTask } from "@/services/api/task-center";
import { bindBackendCanvasGenerationResult } from "@/services/canvas-generation-consumer";
import { projectCanvasGenerationSubmission } from "@/lib/canvas/canvas-generation-live-target";
import { defaultConfig, useConfigStore, useEffectiveConfig, type AiConfig } from "@/stores/use-config-store";
import { CanvasNodeType, type CanvasConnection, type CanvasNodeData } from "@/types/canvas";
import type { CanvasPageUiStoreHook } from "@/stores/canvas/use-canvas-page-ui-store";
import type { createCanvasMediaOperation } from "./canvas-media-operation";
import type { useCanvasGenerationExecutor } from "./use-canvas-generation-executor";

const NODE_STATUS_LOADING = "loading" as const;
const NODE_STATUS_SUCCESS = "success" as const;
const NODE_STATUS_ERROR = "error" as const;
const NODE_STATUS_IDLE = "idle" as const;

function normalizeMaskEditQuality(quality: string | undefined, size: string | undefined) {
    const value = String(quality || "").trim().toLowerCase();
    if (value && value !== "auto" && value !== "any") return value;
    const match = String(size || "").trim().toLowerCase().match(/^(\d+)x(\d+)$/);
    if (!match) return quality || "auto";
    const pixels = Number(match[1]) * Number(match[2]);
    return pixels <= 2_000_000 ? "1k" : pixels <= 4_300_000 ? "2k" : pixels <= 8_294_400 ? "4k" : quality || "auto";
}

export type UseCanvasImageToolsOptions = {
    pageUi: CanvasPageUiStoreHook;
    projectId: string;
    nodesRef: { current: CanvasNodeData[] };
    connectionsRef: { current: CanvasConnection[] };
    handleGenerateNode: ReturnType<typeof useCanvasGenerationExecutor>;
    setNodes: Dispatch<SetStateAction<CanvasNodeData[]>>;
    setConnections: Dispatch<SetStateAction<CanvasConnection[]>>;
    setRunningNodeId: Dispatch<SetStateAction<string | null>>;
    startGenerationRequest: (targetNodeId: string, originNodeId: string, runningId?: string, controller?: AbortController) => AbortController;
    finishGenerationRequest: (targetNodeId: string, controller: AbortController) => void;
    bindGenerationTask: (targetNodeId: string, task: GenerationTask) => void;
    beginMediaOperation: () => ReturnType<typeof createCanvasMediaOperation>;
    persistMediaNodes: (mediaNodes: CanvasNodeData[], mediaConnections?: CanvasConnection[], options?: { signal?: AbortSignal }) => Promise<Map<string, string>>;
    message: useAppProps["message"];
};

export function useCanvasImageTools({
    pageUi, projectId, nodesRef, connectionsRef, handleGenerateNode, setNodes, setConnections, setRunningNodeId,
    startGenerationRequest, finishGenerationRequest, bindGenerationTask, beginMediaOperation, persistMediaNodes, message,
}: UseCanvasImageToolsOptions) {
    const effectiveConfig = useEffectiveConfig();
    const isAiConfigReady = useConfigStore((state) => state.isAiConfigReady);
    const [cropNodeId, setCropNodeId] = useState<string | null>(null);
    const [annotationNodeId, setAnnotationNodeId] = useState<string | null>(null);
    const [maskEditNodeId, setMaskEditNodeId] = useState<string | null>(null);
    const [upscaleNodeId, setUpscaleNodeId] = useState<string | null>(null);
    const [angleNodeId, setAngleNodeId] = useState<string | null>(null);
    const [lightingNodeId, setLightingNodeId] = useState<string | null>(null);
    const [emotionNodeId, setEmotionNodeId] = useState<string | null>(null);

    const toolSupport = useCallback((tool: string, node: CanvasNodeData, config = effectiveConfig) => {
        const canvas = useCanvasStore.getState().openProject(projectId);
        const support = dreamaImageToolSupport(tool, node, config, canvas ? { ...canvas, nodes: nodesRef.current } : { id: projectId, nodes: nodesRef.current });
        if (support?.disabled) message.error(support.reason);
        return support;
    }, [effectiveConfig, message, nodesRef, projectId]);

    const publishEditTargets = useCallback((targets: CanvasNodeData[], edges: CanvasConnection[]) => {
        const nodes = [...nodesRef.current, ...targets];
        const connections = [...connectionsRef.current, ...edges];
        nodesRef.current = nodes;
        connectionsRef.current = connections;
        setNodes(nodes);
        setConnections(connections);
        useCanvasStore.getState().updateProject(projectId, { nodes, connections });
    }, [connectionsRef, nodesRef, projectId, setConnections, setNodes]);

    const submitImageEdit = useCallback((input: Parameters<typeof runCanvasGenerationTaskToConsumer>[0], operation: ReturnType<typeof beginMediaOperation>) => {
        const isCurrent = () => operation.isCurrent() && !input.signal?.aborted && nodesRef.current.some(node => node.id === input.nodeId);
        return runCanvasGenerationTaskToConsumer(input, {
            isCurrent,
            bindTask: task => { if (isCurrent()) bindGenerationTask(input.nodeId, task); },
            onSubmission: metadata => projectCanvasGenerationSubmission(nodesRef, setNodes, [input.nodeId], metadata),
            consumeTask: task => bindBackendCanvasGenerationResult({
                canvasId: input.projectId, nodeId: input.nodeId, task, nodesRef, setNodes, signal: input.signal, isCurrent,
                isNodeOwned: () => nodesRef.current.some(node => node.id === input.nodeId && node.metadata?.taskId === task.id),
            }),
        });
    }, [bindGenerationTask, nodesRef, setNodes]);

    const addImageToolDraft = useCallback((child: CanvasNodeData, sourceId: string) => {
        const nextNodes = [...nodesRef.current, child];
        const nextConnections = [...connectionsRef.current, { id: nanoid(), fromNodeId: sourceId, toNodeId: child.id }];
        // The coordinator captures inputs synchronously, before React commits this render.
        nodesRef.current = nextNodes;
        connectionsRef.current = nextConnections;
        setNodes(nextNodes);
        setConnections(nextConnections);
        const ui = pageUi.getState();
        ui.setHoveredNodeId(null);
        ui.setToolbarNodeId(null);
        ui.setSelectedNodeIds(new Set([child.id]));
        ui.setSelectedConnectionId(null);
        ui.setDialogNodeId(child.id);
    }, [connectionsRef, nodesRef, pageUi, setConnections, setNodes]);

    const clearDeletedImageNodes = useCallback((removedIds: Set<string>) => {
        const clear = (current: string | null) => (current && removedIds.has(current) ? null : current);
        setCropNodeId(clear);
        setAnnotationNodeId(clear);
        setMaskEditNodeId(clear);
        setUpscaleNodeId(clear);
        setAngleNodeId(clear);
        setLightingNodeId(clear);
        setEmotionNodeId(clear);
    }, []);

    useEffect(() => {
        setCropNodeId(null);
        setAnnotationNodeId(null);
        setMaskEditNodeId(null);
        setUpscaleNodeId(null);
        setAngleNodeId(null);
        setLightingNodeId(null);
        setEmotionNodeId(null);
    }, [projectId]);

    const resolveImageEditStyle = useCallback((node: CanvasNodeData, prompt: string, config: AiConfig) => {
        try {
            const runtime = resolveCanvasStyleExecution(nodesRef.current, node, prompt, config, "image");
            return {
                prompt: runtime?.prompt || prompt,
                metadata: runtime ? { styleProfileJson: runtime.profileJson, styleExecutionPlan: runtime.plan } : {},
            };
        } catch (error) {
            message.error(generationErrorMessage(error));
            return null;
        }
    }, [message, nodesRef]);

    const createImageReversePromptNodes = useCallback((node: CanvasNodeData) => {
        const isVideo = node.type === CanvasNodeType.Video;
        if (isVideo && !canvasVideoReversePromptAvailable(effectiveConfig)) {
            message.warning("当前本机 Codex 渠道不支持视频反推，可先截取关键帧后使用图片反推提示词");
            return;
        }
        if ((node.type !== CanvasNodeType.Image && !isVideo) || (!node.metadata?.content && !node.metadata?.storageKey)) {
            message.warning(isVideo ? "视频节点为空，无法反推提示词" : "图片节点为空，无法反推提示词");
            return;
        }
        const reverseInstruction = isVideo ? VIDEO_PROMPT_REVERSE : IMAGE_PROMPT_REVERSE;
        const gap = 96;
        const textSpec = NODE_DEFAULT_SIZE[CanvasNodeType.Text];
        const resultSpec = NODE_DEFAULT_SIZE[CanvasNodeType.Text];
        const centerY = node.position.y + node.height / 2;
        const textNode = {
            ...createCanvasNode(CanvasNodeType.Text, { x: node.position.x + node.width + gap + textSpec.width / 2, y: centerY }, { content: reverseInstruction, prompt: reverseInstruction, status: NODE_STATUS_SUCCESS, fontSize: 14 }),
            title: isVideo ? "视频反推提示词" : "反推提示词",
        };
        const resultNode = {
            ...createCanvasNode(CanvasNodeType.Text, { x: textNode.position.x + textNode.width + gap + resultSpec.width / 2, y: centerY }, {
                content: "", generationMode: "text", model: effectiveConfig.textModel || effectiveConfig.model || defaultConfig.textModel,
                count: 1, composerContent: isVideo ? "参考视频：@视频1\n任务说明：@文本1" : "参考图片：@图片1\n任务说明：@文本1",
            }),
            title: isVideo ? "视频反推提示词结果" : "反推提示词结果",
        };
        setNodes((current) => [...current, textNode, resultNode]);
        setConnections((current) => [...current, { id: nanoid(), fromNodeId: node.id, toNodeId: resultNode.id }, { id: nanoid(), fromNodeId: textNode.id, toNodeId: resultNode.id }]);
        pageUi.getState().setSelectedNodeIds(new Set([resultNode.id]));
        pageUi.getState().setSelectedConnectionId(null);
        pageUi.getState().setDialogNodeId(resultNode.id);
        pageUi.getState().setContextMenu(null);
    }, [pageUi, projectId, effectiveConfig, message, setConnections, setNodes]);

    const openPortraitTextureEditor = useCallback((node: CanvasNodeData) => {
        const support = toolSupport("portraitTexture", node);
        if (support?.disabled) return;
        if (node.type !== CanvasNodeType.Image || !node.metadata?.content && !node.metadata?.storageKey) {
            message.warning("图片节点为空，无法调节人物质感");
            return;
        }
        const portraitTextureSettings = { ...DEFAULT_PORTRAIT_TEXTURE_SETTINGS, ...node.metadata?.portraitTexture, ...node.metadata?.imageTool?.portraitTexture };
        if (support) {
            addImageToolDraft(createImageToolGenerationNode({ source: node, id: nanoid(), title: `${node.title || "图片"} · 人物质感`, kind: "portraitTexture", config: buildGenerationConfig(effectiveConfig, node, "image"), portraitTexture: portraitTextureSettings }), node.id);
            return;
        }
        const child = createPortraitTextureNode(node, nanoid());
        child.metadata = { ...child.metadata, portraitTexture: portraitTextureSettings };
        const { setHoveredNodeId, setToolbarNodeId, setSelectedNodeIds, setSelectedConnectionId, setDialogNodeId } = pageUi.getState();
        setHoveredNodeId(null);
        setToolbarNodeId(null);
        setNodes((current) => [...current, child]);
        setConnections((current) => [...current, { id: nanoid(), fromNodeId: node.id, toNodeId: child.id }]);
        setSelectedNodeIds(new Set([child.id]));
        setSelectedConnectionId(null);
        setDialogNodeId(child.id);
    }, [addImageToolDraft, effectiveConfig, pageUi, projectId, message, setConnections, setNodes, toolSupport]);

    const cropImageNode = useCallback(async (node: CanvasNodeData, crop: CanvasImageCropRect) => {
        const operation = beginMediaOperation();
        if (!operation.isCurrent()) return;
        if (!node.metadata?.content) return;
        try {
            const source = await operation.wait(resolveImageUrl(node.metadata.storageKey, node.metadata.content, { cacheMiss: true }));
            const cropped = await operation.wait(cropDataUrl(source, crop));
            const image = await operation.wait(uploadImage(cropped));
            const size = fitNodeSize(image.width, image.height, node.width, node.height);
            const childId = nanoid();
            const child: CanvasNodeData = { id: childId, type: CanvasNodeType.Image, title: `${node.title || "图片"} · 裁剪`, position: { x: node.position.x + node.width + 96, y: node.position.y }, width: size.width, height: size.height, metadata: mediaResultMetadata("derived", { ...imageMetadata(image), prompt: node.metadata?.prompt, generatedFromNodeId: node.id,
                workflowOperation: { id: "image.crop", version: 1, config: { x: crop.x, y: crop.y, width: crop.width, height: crop.height }, inputs: [{ nodeId: node.id, port: "image" }] },
            }) };
            const connection = { id: nanoid(), fromNodeId: node.id, toNodeId: childId };
            setNodes((current) => [...current, child]);
            setConnections((current) => [...current, connection]);
            pageUi.getState().setSelectedNodeIds(new Set([childId]));
            pageUi.getState().setDialogNodeId(childId);
            setCropNodeId(null);
            await operation.wait(persistMediaNodes([child], [connection], {}));
        } catch (error) {
            if (!operation.isCurrent()) return;
            message.error(error instanceof Error ? `裁切失败：${error.message}` : "裁切失败，请重试");
        }
    }, [beginMediaOperation, pageUi, projectId, message, persistMediaNodes, setConnections, setNodes]);

    const saveAnnotatedImageNode = useCallback(async (node: CanvasNodeData, dataUrl: string) => {
        const operation = beginMediaOperation();
        if (!operation.isCurrent()) return;
        try {
            const image = await operation.wait(uploadImage(dataUrl));
            const size = fitNodeSize(image.width, image.height, node.width, node.height);
            const childId = nanoid();
            const child: CanvasNodeData = { id: childId, type: CanvasNodeType.Image, title: `${node.title || "图片"} · 标注`, position: { x: node.position.x + node.width + 96, y: node.position.y }, width: size.width, height: size.height, metadata: mediaResultMetadata("derived", { ...imageMetadata(image), prompt: node.metadata?.prompt, generatedFromNodeId: node.id }) };
            const connection = { id: nanoid(), fromNodeId: node.id, toNodeId: childId };
            setNodes((current) => [...current, child]);
            setConnections((current) => [...current, connection]);
            pageUi.getState().setSelectedNodeIds(new Set([childId]));
            pageUi.getState().setSelectedConnectionId(null);
            pageUi.getState().setDialogNodeId(null);
            setAnnotationNodeId(null);
            await operation.wait(persistMediaNodes([child], [connection], {}));
            message.success("标注图片已保存为新节点");
        } catch (error) {
            if (operation.isCurrent()) throw error;
        }
    }, [beginMediaOperation, pageUi, projectId, message, persistMediaNodes, setConnections, setNodes]);

    const splitImageNode = useCallback(async (node: CanvasNodeData, params: CanvasImageSplitParams) => {
        const operation = beginMediaOperation();
        if (!operation.isCurrent()) return;
        try {
            if (!node.metadata?.content || !isValidGridSplit(params)) return;
            const source = await operation.wait(resolveImageUrl(node.metadata.storageKey, node.metadata.content, { cacheMiss: true }));
            const pieces = await operation.wait(splitDataUrl(source, params));
            const sizedPieces = await operation.wait(Promise.all(pieces.map(async (piece) => {
                const image = await operation.wait(uploadImage(piece.dataUrl));
                return { piece, image, size: fitNodeSize(image.width, image.height) };
            })));
            const positions = layoutGridSplitCells(
                { x: node.position.x + node.width + 96, y: node.position.y },
                sizedPieces.map(({ piece, size }) => ({ row: piece.row, column: piece.column, width: size.width, height: size.height })),
            );
            const childNodes = sizedPieces.map(({ piece, image, size }, index) => ({
                id: nanoid(), type: CanvasNodeType.Image, title: `${node.title || "图片"} · 宫格 ${piece.row + 1}-${piece.column + 1}`,
                position: positions[index] || { x: node.position.x + node.width + 96, y: node.position.y }, width: size.width, height: size.height,
                metadata: mediaResultMetadata("derived", { ...imageMetadata(image), prompt: node.metadata?.prompt, manualSize: true, generatedFromNodeId: node.id }),
            } satisfies CanvasNodeData));
            setNodes((current) => [...current, ...childNodes]);
            setConnections((current) => [...current, ...childNodes.map((child) => ({ id: nanoid(), fromNodeId: node.id, toNodeId: child.id }))]);
            pageUi.getState().setSelectedNodeIds(new Set(childNodes.map((child) => child.id)));
            pageUi.getState().setSelectedConnectionId(null);
            pageUi.getState().setDialogNodeId(null);
            await operation.wait(persistMediaNodes(childNodes, [], {}));
            message.success(`已切分为 ${childNodes.length} 个子节点`);
        } catch (error) {
            if (operation.isCurrent()) throw error;
        }
    }, [beginMediaOperation, pageUi, projectId, message, persistMediaNodes, setConnections, setNodes]);

    const maskEditImageNode = useCallback(async (node: CanvasNodeData, payload: CanvasImageMaskEditPayload) => {
        const selectedNode = payload.generationConfig?.model || payload.generationConfig?.imageModel
            ? { ...node, metadata: { ...node.metadata, model: payload.generationConfig.model || payload.generationConfig.imageModel } } : node;
        if (toolSupport("maskEdit", selectedNode)?.disabled) return;
        const operation = beginMediaOperation();
        if (!operation.isCurrent()) return;
        try {
            const baseGenerationConfig = buildGenerationConfig(effectiveConfig, node, "image");
            const selectedModel = payload.generationConfig?.model || payload.generationConfig?.imageModel || baseGenerationConfig.model;
            const modelDefaults = defaultImageParamsForModel(baseGenerationConfig, selectedModel);
            const generationConfig = {
                ...baseGenerationConfig,
                ...payload.generationConfig,
                model: selectedModel,
                imageModel: payload.generationConfig?.imageModel || payload.generationConfig?.model || effectiveConfig.imageModel,
                quality: normalizeMaskEditQuality(payload.generationConfig?.quality || node.metadata?.quality || baseGenerationConfig.quality || modelDefaults.quality, payload.generationConfig?.size || node.metadata?.size || baseGenerationConfig.size || modelDefaults.size),
                count: String(payload.generationConfig?.count || 1),
                // 原图像素尺寸不是模型的输出尺寸合同；非高级设置时使用模型默认尺寸，避免把节点尺寸误发给上游。
                size: payload.generationConfig?.size || node.metadata?.size || modelDefaults.size,
            };
            if (!isAiConfigReady(generationConfig, generationConfig.model)) {
                navigateToSettings({ continueCreation: true });
                return;
            }
            const userPrompt = payload.prompt.trim();
            const reference = nodeReferenceImage(node);
            if (!reference) throw new Error("局部重绘原图不可用，请重新选择图片");
            const useGuide = maskEditNeedsGuide(generationConfig);
            const styleExecution = resolveImageEditStyle(node, userPrompt, generationConfig);
            if (!styleExecution) return;
            const { prompt: effectivePrompt, metadata: styleMetadata } = styleExecution;
            const source = await operation.wait(freezeImageEditSource(reference));
            const savedMask = await operation.wait(uploadImage(payload.maskDataUrl));
            const mask = { id: `${node.id}-mask`, name: "mask.png", type: "image/png", storageKey: savedMask.storageKey, dataUrl: payload.maskDataUrl };
            const inputs = await operation.wait(maskEditInputs(source, mask, useGuide, effectivePrompt));
            const maskEdit = { sourceNodeId: node.id, maskStorageKey: savedMask.storageKey, useGuide };
            const requestedCount = Math.max(1, Number(generationConfig.count) || 1);
            const generationMetadata = { ...buildImageGenerationMetadata("edit", generationConfig, requestedCount, [source]), maskEdit };
            setMaskEditNodeId(null);
            const rootId = nanoid();
            const childIds = requestedCount > 1 ? Array.from({ length: requestedCount }, () => nanoid()) : [];
            const targetIds = requestedCount > 1 ? childIds : [rootId];
            const imageSize = { width: node.width, height: node.height };
            const preferredPosition = { x: node.position.x + node.width + 96, y: node.position.y };
            const rootPosition = findAvailableGenerationGroupPosition(nodesRef.current, preferredPosition, imageGenerationGroupSize(imageSize, imageSize, childIds.length));
            const rootNode: CanvasNodeData = {
                id: rootId, type: CanvasNodeType.Image, title: userPrompt.slice(0, 32) || "局部编辑结果",
                position: rootPosition, width: node.width, height: node.height,
                metadata: {
                    ...canvasGenerationPromptMetadata(userPrompt, effectivePrompt), status: NODE_STATUS_LOADING,
                    isBatchRoot: requestedCount > 1, batchChildIds: requestedCount > 1 ? childIds : undefined,
                    batchFailedCount: requestedCount > 1 ? 0 : undefined,
                    imageBatchExpanded: requestedCount > 1 ? true : undefined,
                    ...generationMetadata, ...styleMetadata,
                },
            };
            const childNodes: CanvasNodeData[] = childIds.map((id, index) => ({
                id, type: CanvasNodeType.Image, title: `${userPrompt.slice(0, 28) || "局部编辑结果"} · ${index + 1}`,
                position: imageGenerationChildPosition(rootNode.position, rootNode.width, imageSize, index),
                width: node.width, height: node.height,
                metadata: {
                    ...canvasGenerationPromptMetadata(userPrompt, effectivePrompt), status: NODE_STATUS_LOADING,
                    batchRootId: rootId, ...generationMetadata, ...styleMetadata,
                },
            }));
            setRunningNodeId(rootId);
            publishEditTargets([rootNode, ...childNodes], [{ id: nanoid(), fromNodeId: node.id, toNodeId: rootId }, ...childIds.map(childId => ({ id: nanoid(), fromNodeId: rootId, toNodeId: childId }))]);
            pageUi.getState().setSelectedNodeIds(new Set([rootId, ...childIds]));
            pageUi.getState().setSelectedConnectionId(null);
            pageUi.getState().setDialogNodeId(rootId);
            const controller = startGenerationRequest(rootId, node.id, rootId);
            targetIds.forEach((targetId) => startGenerationRequest(targetId, node.id, rootId, controller));
            let hasSuccess = false;
            let failureCount = 0;
            let representativeError: string | undefined;
            try {
                await operation.wait(Promise.all(targetIds.map(async (targetId) => {
                    try {
                        await operation.wait(submitImageEdit({
                            projectId, nodeId: targetId, mode: "image", ...inputs, config: { ...generationConfig, count: "1" },
                            signal: controller.signal, metadata: { sourceNodeId: node.id, edit: useGuide ? "mask-guide" : "mask", ...styleMetadata },
                        }, operation));
                        hasSuccess = true;
                    } catch (error) {
                        if (!operation.isCurrent()) return;
                        if (isGenerationCanceled(error)) return;
                        failureCount += 1;
                        const failure = canvasGenerationFailureMetadata(error, { mode: "image", ...inputs });
                        representativeError = failure.errorDetails;
                        setNodes((current) => current.map((item) => (item.id === targetId ? { ...item, metadata: { ...item.metadata, status: NODE_STATUS_ERROR, ...failure } } : item)));
                    } finally {
                        if (operation.isCurrent()) finishGenerationRequest(targetId, controller);
                    }
                })));
                if (controller.signal.aborted) {
                    setNodes((current) => {
                        const cancelled = cancelIncompleteImageBatch(rootId, childIds, current, []);
                        if (cancelled.removedIds.length) {
                            const removed = new Set(cancelled.removedIds);
                            setConnections((connections) => connections.filter((connection) => !removed.has(connection.fromNodeId) && !removed.has(connection.toNodeId)));
                        }
                        return cancelled.nodes.map((item) => {
                            if (item.id !== rootId || item.metadata?.content) return item;
                            return { ...item, metadata: { ...item.metadata, status: NODE_STATUS_IDLE, errorDetails: undefined } };
                        });
                    });
                    return;
                }
                if (failureCount > 0) message.error(hasSuccess ? "部分局部编辑失败" : representativeError || "局部编辑失败");
                setNodes((current) => current.map((item) => {
                    if (item.id !== rootId) return item;
                    if (requestedCount > 1) return reconcileImageBatchRoot(item, current);
                    return {
                        ...item,
                        metadata: {
                            ...item.metadata, status: hasSuccess ? NODE_STATUS_SUCCESS : NODE_STATUS_ERROR,
                            batchFailedCount: requestedCount > 1 ? failureCount : undefined,
                            ...(hasSuccess ? { errorDetails: undefined } : { errorDetails: representativeError || "局部编辑失败" }),
                        },
                    };
                }));
            } finally {
                if (operation.isCurrent()) {
                    if (requestedCount > 1) finishGenerationRequest(rootId, controller);
                    setRunningNodeId(null);
                }
            }
        } catch (error) {
            if (operation.isCurrent()) throw error;
        }
    }, [beginMediaOperation, pageUi, effectiveConfig, finishGenerationRequest, isAiConfigReady, message, nodesRef, projectId, publishEditTargets, resolveImageEditStyle, setConnections, setNodes, setRunningNodeId, startGenerationRequest, submitImageEdit, toolSupport]);

    const upscaleImageNode = useCallback(async (node: CanvasNodeData, params: CanvasImageUpscaleParams) => {
        const operation = beginMediaOperation();
        if (!operation.isCurrent()) return;
        try {
            if (!node.metadata?.content) return;
            setUpscaleNodeId(null);
            const source = await operation.wait(resolveImageUrl(node.metadata.storageKey, node.metadata.content, { cacheMiss: true }));
            const upscaled = await operation.wait(upscaleDataUrl(source, params));
            const image = await operation.wait(uploadImage(upscaled));
            const size = fitNodeSize(image.width, image.height);
            const childId = nanoid();
            const child: CanvasNodeData = { id: childId, type: CanvasNodeType.Image, title: `${node.title || "图片"} · 放大`, position: { x: node.position.x + node.width + 96, y: node.position.y }, width: size.width, height: size.height, metadata: mediaResultMetadata("derived", { ...imageMetadata(image), prompt: node.metadata?.prompt, generatedFromNodeId: node.id,
                workflowOperation: { id: "image.resize", version: 1, config: { longEdge: params.targetLongEdge, algorithm: params.algorithm }, inputs: [{ nodeId: node.id, port: "image" }] },
            }) };
            setNodes((current) => [...current, child]);
            setConnections((current) => [...current, { id: nanoid(), fromNodeId: node.id, toNodeId: childId }]);
            pageUi.getState().setSelectedNodeIds(new Set([childId]));
            pageUi.getState().setDialogNodeId(childId);
            await operation.wait(persistMediaNodes([child], [], {}));
        } catch (error) {
            if (operation.isCurrent()) throw error;
        }
    }, [beginMediaOperation, pageUi, projectId, persistMediaNodes, setConnections, setNodes]);

    const generateAngleNode = useCallback(async (node: CanvasNodeData, params: CanvasImageAngleParams) => {
        if (toolSupport("angle", node)?.disabled || !nodeReferenceImage(node)) return;
        const config = buildGenerationConfig(effectiveConfig, node, "image");
        if (!isAiConfigReady(config, config.model)) {
            navigateToSettings({ continueCreation: true });
            return;
        }
        const child = createImageToolGenerationNode({
            source: node, id: nanoid(), title: buildAngleLabel(params), kind: "angle",
            instruction: buildAnglePrompt(params), config,
        });
        addImageToolDraft(child, node.id);
        setAngleNodeId(null);
        await handleGenerateNode(child.id, "image", child.metadata!.composerContent!);
    }, [addImageToolDraft, effectiveConfig, handleGenerateNode, isAiConfigReady, toolSupport]);

    const generateLightingNode = useCallback((node: CanvasNodeData, options: CanvasImageLightingOptions, prompt: string) => {
        const support = toolSupport("lighting", node);
        if (support?.disabled) return;
        if (support) {
            addImageToolDraft(createImageToolGenerationNode({ source: node, id: nanoid(), title: buildLightingLabel(options), kind: "lighting", instruction: prompt, config: buildGenerationConfig(effectiveConfig, node, "image") }), node.id);
            setLightingNodeId(null);
            return;
        }
        const generationConfig = { ...buildGenerationConfig(effectiveConfig, node, "image"), count: "1" };
        const childId = nanoid();
        const imageSpec = NODE_DEFAULT_SIZE[CanvasNodeType.Image];
        const title = buildLightingLabel(options);
        const source = nodeReferenceImage(node);
        if (!source) return;
        const generationMetadata = buildImageGenerationMetadata("edit", generationConfig, 1, [source]);
        setLightingNodeId(null);
        setNodes((current) => [...current, {
            id: childId, type: CanvasNodeType.Image, title, position: { x: node.position.x + node.width + 96, y: node.position.y },
            width: imageSpec.width, height: imageSpec.height,
            metadata: { prompt, status: NODE_STATUS_IDLE, generationMode: "image", ...generationMetadata },
        }]);
        setConnections((current) => [...current, { id: nanoid(), fromNodeId: node.id, toNodeId: childId }]);
        pageUi.getState().setSelectedNodeIds(new Set([childId]));
        pageUi.getState().setSelectedConnectionId(null);
        pageUi.getState().setDialogNodeId(childId);
    }, [addImageToolDraft, pageUi, projectId, effectiveConfig, setConnections, setLightingNodeId, setNodes, toolSupport]);

    const generateEmotionNode = useCallback(async (node: CanvasNodeData, payload: CanvasImageEmotionPayload) => {
        if (toolSupport("emotion", node)?.disabled) return;
        const operation = beginMediaOperation();
        if (!operation.isCurrent()) return;
        const baseConfig = buildGenerationConfig(effectiveConfig, node, "image");
        const { generationConfig, editPlan } = emotionGenerationSettings(baseConfig, payload.editRegion);
        const providerSize = generationConfig.size;
        if (!isAiConfigReady(generationConfig, generationConfig.model)) { navigateToSettings({ continueCreation: true }); return; }
        const reference = nodeReferenceImage(node);
        if (!reference) throw new Error("表情编辑原图不可用，请重新选择图片");
        const source = await operation.wait(freezeImageEditSource(reference));
        const editReference = { id: `${node.id}-${payload.presetId}-edit-region`, name: "emotion-edit-region.png", type: "image/png", dataUrl: payload.sourceDataUrl };
        const characterReference = { id: `${node.id}-${payload.presetId}-character`, name: `${payload.characterName}-face.jpg`, type: "image/jpeg", dataUrl: payload.characterDataUrl };
        const childId = nanoid();
        const styleExecution = resolveImageEditStyle(node, payload.prompt, generationConfig);
        if (!styleExecution) return;
        const { prompt: effectivePrompt, metadata: styleMetadata } = styleExecution;
        const providerPrompt = normalizeEmotionPromptForProvider(effectivePrompt);
        const generationMetadata = buildImageGenerationMetadata("edit", generationConfig, 1, [source]);
        const emotionEdit = { sourceNodeId: node.id, characterName: payload.characterName, presetId: payload.presetId, intimacy: payload.intimacy, arousal: payload.arousal, label: payload.label, faceBox: payload.faceBox, editRegion: payload.editRegion, sourceWidth: payload.imageWidth, sourceHeight: payload.imageHeight, providerSize, editMode: editPlan.mode };
        if (editPlan.notice) message.info(editPlan.notice);
        setEmotionNodeId(null);
        setRunningNodeId(childId);
        publishEditTargets([{ id: childId, type: CanvasNodeType.Image, title: `${payload.characterName} · ${payload.label}`, position: { x: node.position.x + node.width + 96, y: node.position.y }, width: node.width, height: node.height, metadata: { prompt: providerPrompt, status: NODE_STATUS_LOADING, ...generationMetadata, ...styleMetadata, emotionEdit } }], [{ id: nanoid(), fromNodeId: node.id, toNodeId: childId }]);
        pageUi.getState().setSelectedNodeIds(new Set([childId]));
        pageUi.getState().setSelectedConnectionId(null);
        pageUi.getState().setDialogNodeId(childId);
        const controller = startGenerationRequest(childId, node.id, childId);
        try {
            const mask = emotionProviderMask(editPlan, { id: `${node.id}-emotion-mask`, name: "emotion-mask.png", type: "image/png", dataUrl: payload.maskDataUrl });
            await operation.wait(submitImageEdit({
                projectId, nodeId: childId, mode: "image", prompt: providerPrompt, config: generationConfig,
                referenceImages: [editReference, characterReference], mask, signal: controller.signal,
                metadata: { sourceNodeId: node.id, edit: "emotion", emotionEditMode: editPlan.mode, emotion: emotionEdit, ...styleMetadata },
            }, operation));
        } catch (error) {
            if (!operation.isCurrent()) return;
            if (isGenerationCanceled(error)) return;
            const failure = canvasGenerationFailureMetadata(error, { mode: "image", prompt: providerPrompt, referenceImages: [editReference, characterReference] });
            message.error(failure.errorDetails);
            setNodes((current) => current.map((item) => item.id === childId ? { ...item, metadata: { ...item.metadata, status: NODE_STATUS_ERROR, ...failure } } : item));
        } finally {
            if (operation.isCurrent()) {
                finishGenerationRequest(childId, controller);
                setRunningNodeId(null);
            }
        }
    }, [beginMediaOperation, pageUi, effectiveConfig, finishGenerationRequest, isAiConfigReady, message, nodesRef, projectId, publishEditTargets, resolveImageEditStyle, setConnections, setNodes, setRunningNodeId, startGenerationRequest, submitImageEdit, toolSupport]);

    return {
        clearDeletedImageNodes, angleNodeId, lightingNodeId, emotionNodeId, annotationNodeId,
        createImageReversePromptNodes, openPortraitTextureEditor, cropImageNode, cropNodeId,
        generateAngleNode, generateLightingNode, maskEditImageNode, maskEditNodeId, saveAnnotatedImageNode,
        setAngleNodeId, setLightingNodeId, generateEmotionNode, setEmotionNodeId, setAnnotationNodeId,
        setCropNodeId, setMaskEditNodeId, setUpscaleNodeId, splitImageNode, upscaleImageNode, upscaleNodeId,
    };
}
