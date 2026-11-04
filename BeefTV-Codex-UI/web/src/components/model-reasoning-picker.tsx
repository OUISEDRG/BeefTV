import { App, Button, Select, Tooltip } from "antd";
import { useIsMutating, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { modelReasoningQueryKey, modelReasoningTarget, prepareReasoningUpdate, reasoningSelectOptions, type ModelReasoningTarget } from "@/lib/model-reasoning";
import { getModelReasoning, saveModelReasoning, type ModelReasoningSnapshot, type ModelReasoningUpdate } from "@/services/api/model-reasoning";
import { ApiError } from "@/services/api/request";
import type { AiConfig } from "@/stores/use-config-store";

type Props = { config: AiConfig; modelKey: string; disabled?: boolean; className?: string };

/** The same per-model preference is shared by canvas text and assistant requests. */
export function ModelReasoningPicker({ config, modelKey, disabled = false, className = "" }: Props) {
    const target = modelReasoningTarget(config, modelKey);
    return target ? <LocalModelReasoningPicker target={target} disabled={disabled} className={className} /> : null;
}

function LocalModelReasoningPicker({ target, disabled, className }: { target: ModelReasoningTarget; disabled: boolean; className: string }) {
    const { message } = App.useApp();
    const queryClient = useQueryClient();
    const queryKey = modelReasoningQueryKey(target);
    const query = useQuery({
        queryKey,
        queryFn: ({ signal }) => getModelReasoning(target.channelId, signal),
        staleTime: 15_000,
        retry: false,
    });
    const saving = useIsMutating({ mutationKey: ["model-reasoning-save", target.channelId] }) > 0;
    const mutation = useMutation({
        mutationKey: ["model-reasoning-save", target.channelId],
        mutationFn: (variables: { update: ModelReasoningUpdate; queryKey: ReturnType<typeof modelReasoningQueryKey> }) => saveModelReasoning(variables.update),
        retry: false,
        onMutate: variables => queryClient.cancelQueries({ queryKey: variables.queryKey }),
        onSuccess: (snapshot, variables) => {
            queryClient.setQueryData<ModelReasoningSnapshot>(variables.queryKey, snapshot);
            message.success("推理强度已保存，从下一次新请求生效；当前任务保持原设置。");
        },
        onError: (error, variables) => {
            if (error instanceof ApiError && (error.status === 409 || error.reason === "settings_conflict")) {
                void queryClient.invalidateQueries({ queryKey: variables.queryKey });
                message.warning("推理设置已在其他位置更新，正在重新读取；请核对后再选择。");
            } else {
                message.error(error instanceof Error ? error.message : "推理强度保存失败，请重试。");
            }
        },
    });
    if (query.error instanceof ApiError && query.error.reason === "reasoning_unsupported") return null;
    const model = query.data?.models.find(item => item.id === target.model);
    if (!query.error && query.data && !model) return null;
    if (query.error) return (
        <Tooltip title={query.error instanceof Error ? query.error.message : "暂时无法读取推理强度"}>
            <Button type="text" size="small" className={`shrink-0 ${className}`} disabled={disabled || query.isFetching} aria-label="重新读取推理强度" icon={<RefreshCw size={12} />} onClick={() => void query.refetch()}>推理设置</Button>
        </Tooltip>
    );
    const value = query.data?.overrides[target.model] || "";
    return (
        <Tooltip title={disabled ? "当前任务的模型和推理强度已固定，结束后可更改" : "同一模型的文本生成和助手共用此强度；修改从下一次新请求开始生效"}>
            <span className="inline-flex shrink-0" data-canvas-no-zoom onPointerDown={event => event.stopPropagation()} onMouseDown={event => event.stopPropagation()}>
            <Select<string>
                aria-label="推理强度"
                className={`shrink-0 !min-w-24 !text-[var(--fs-tiny)] ${className}`}
                size="small"
                variant="borderless"
                placement="topLeft"
                popupMatchSelectWidth={false}
                value={model ? value : undefined}
                placeholder="读取推理强度…"
                loading={query.isLoading || saving}
                disabled={disabled || saving || !model || !query.data}
                options={model ? reasoningSelectOptions(model) : []}
                labelRender={option => `推理 ${option.label || "默认"}`}
                onChange={effort => {
                    if (disabled || saving || !query.data || effort === value) return;
                    try {
                        const update = prepareReasoningUpdate(target, query.data, effort);
                        // Capture the old model/key before waiting, even if the user switches models meanwhile.
                        mutation.mutate({ update, queryKey });
                    } catch (error) {
                        message.error(error instanceof Error ? error.message : "无法保存推理强度。");
                    }
                }}
            />
            </span>
        </Tooltip>
    );
}
