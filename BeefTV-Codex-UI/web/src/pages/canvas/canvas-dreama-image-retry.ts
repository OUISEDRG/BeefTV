import { findGenerationTaskByOperation, queryGenerationTask, recoverTask, retryGenerationTask, waitForGenerationTask, type GenerationTask } from "@/services/api/task-center";
import { isRegenerateAllowed, isRegenerateConfirm, normalizeTaskOutcome, taskOutcomeCopy } from "@/lib/task-outcome";
import { modelCapabilityConfigFor } from "@/lib/model-capabilities";
import type { CanvasNodeData, CanvasConnection } from "@/types/canvas";
import type { ConfirmedGenerationInputs } from "./canvas-assistant-proposal-snapshot";

const hasOutput = (node: CanvasNodeData) => Boolean(node.metadata?.content || node.metadata?.storageKey);

/** Only unsubmitted single outputs may be rebuilt. Accepted tasks retain their persisted billing inputs. */
export function dreamaUnsubmittedImageRetryInputs(node: CanvasNodeData, inputs: ConfirmedGenerationInputs): ConfirmedGenerationInputs {
    if (modelCapabilityConfigFor(inputs.config, node.metadata?.model || inputs.config.imageModel).image?.fixedOutputs) throw new Error("原生多输出任务缺少原任务身份，无法安全重试，请先核对原任务");
    const root = node.metadata?.batchRootId ? inputs.nodes.find(item => item.id === node.metadata?.batchRootId) : undefined;
    if (node.metadata?.batchRootId && !root) throw new Error("原图片批次已不存在，无法确认重试归属");
    const originalId = root?.metadata?.generatedFromNodeId || node.metadata?.generatedFromNodeId;
    const original = inputs.nodes.find(item => item.id === originalId);
    const source = root || original || node;
    const referenceOwner = inputs.connections.some(edge => edge.toNodeId === source.id) ? source : original || source;
    const metadata = {
        ...node.metadata, count: 1, generationResultPlacement: "replace-node" as const,
        imageTool: node.metadata?.imageTool || source.metadata?.imageTool,
        isBatchRoot: false, batchChildIds: undefined, generationOutputNodeIds: undefined,
    };
    const target = { ...node, parentId: node.parentId || source.parentId || original?.parentId, metadata };
    const connections: CanvasConnection[] = inputs.connections.filter(edge => edge.toNodeId !== node.id);
    for (const edge of inputs.connections.filter(edge => edge.toNodeId === referenceOwner.id && edge.fromNodeId !== node.id)) {
        connections.push({ ...edge, id: `retry:${node.id}:${edge.id}`, toNodeId: node.id });
    }
    return { ...inputs, nodes: inputs.nodes.map(item => item.id === node.id ? target : item), connections, config: { ...inputs.config, count: "1", canvasImageCount: "1" } };
}

export async function reconcileDreamaImageTask(input: {
    projectId: string; node: CanvasNodeData; nodes: CanvasNodeData[]; signal: AbortSignal;
    bind: (nodeId: string, task: GenerationTask) => void; consume: (nodeId: string, task: GenerationTask) => Promise<void>;
    confirm: (task: GenerationTask) => Promise<boolean>;
    isCurrent: () => boolean;
    claimTask: (taskId: string) => boolean;
}): Promise<boolean> {
    const { node, signal } = input;
    const check = () => {
        signal.throwIfAborted();
        if (!input.isCurrent()) throw new DOMException("Canvas changed", "AbortError");
    };
    const taskId = node.metadata?.taskId;
    const operationId = node.metadata?.taskClientOperationId;
    if (!taskId && !operationId) {
        if (["submitting", "unknown", "accepted"].includes(node.metadata?.generationSubmissionState || "")) throw new Error("原提交缺少任务身份，需先核对原任务，不能重新生成");
        if (node.metadata?.status !== "idle" && node.metadata?.generationSubmissionState !== "rejected" && node.metadata?.generationErrorCode !== "request_not_sent" && node.metadata?.taskOutcome?.reason !== "not_charged") throw new Error("无法确认原请求未提交，请先核对原任务，不能重新生成");
        return false;
    }
    let task = taskId ? await queryGenerationTask(taskId, { signal }) : await findGenerationTaskByOperation(operationId!, input.projectId, signal);
    check();
    if (!task) throw new Error("尚未查到原提交任务，请稍后核对，不能重新生成");
    if (task.type !== "canvas_image" || task.projectId !== input.projectId) throw new Error("原任务类型或画布归属不一致，不能重试");
    if (!input.claimTask(task.id)) return true;
    const originalOperationId = operationId || task.clientOperationId;
    const siblings = input.nodes.filter(item => !item.metadata?.batchChildIds?.length && (item.id === node.id || item.metadata?.taskId === task!.id || Boolean(originalOperationId && item.metadata?.taskClientOperationId === originalOperationId)));
    const targets = siblings.filter(item => !hasOutput(item));
    if (!targets.length) return true;
    const update = (next: GenerationTask) => { if (input.isCurrent()) targets.forEach(target => input.bind(target.id, next)); };
    const outcome = normalizeTaskOutcome(task.outcome);
    if (!outcome) throw new Error("原任务尚无权威执行状态，请先核对原任务");
    update(task);
    if (outcome.phase === "retrievable") {
        // Manual recovery may replay an unknown request with its old key. Only
        // a receipt/provider id or completed delivery authorizes this recovery path.
        if (task.stage === "submission_unknown" || outcome.reason === "submission_unknown" || task.status !== "succeeded" && !task.receiptRecorded && !task.providerRequestId?.trim()) throw new Error("原提交结果尚不明确，已查询原任务，请等待对账");
        task = await recoverTask(task.id, { signal });
    } else if (outcome.phase === "failed") {
        if (siblings.some(hasOutput)) throw new Error("该任务已有输出，请取回原任务剩余结果，不能重新生成整批");
        if (isRegenerateConfirm(outcome)) { if (!await input.confirm(task)) return true; }
        else if (!isRegenerateAllowed(outcome)) throw new Error(taskOutcomeCopy(outcome).message);
        check();
        task = await retryGenerationTask(task.id, { signal });
    }
    check();
    update(task);
    const completed = await waitForGenerationTask(task.id, { signal, initialTask: task, onTaskUpdate: update });
    check();
    if (input.isCurrent()) for (const target of targets) await input.consume(target.id, completed);
    return true;
}

export function dreamaImageRetryIdentity(node: CanvasNodeData): string {
    return node.metadata?.taskId ? `task:${node.metadata.taskId}` : node.metadata?.taskClientOperationId ? `operation:${node.metadata.taskClientOperationId}` : `node:${node.id}`;
}
