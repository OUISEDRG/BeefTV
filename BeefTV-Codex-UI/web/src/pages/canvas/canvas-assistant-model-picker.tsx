import { Select } from "antd";
import { ModelReasoningPicker } from "@/components/model-reasoning-picker";
import { modelReasoningTarget } from "@/lib/model-reasoning";
import { assistantModelOptions, resolveAssistantModel } from "@/lib/assistant-model";
import { modelDisplayName, resolveModelChannel, useConfigStore } from "@/stores/use-config-store";

export function CanvasAssistantModelPicker({ busy }: { busy: boolean }) {
    const config = useConfigStore((state) => state.config);
    const selected = resolveAssistantModel(config);
    const hasReasoning = Boolean(modelReasoningTarget(config, selected));
    const options = assistantModelOptions(config).map((value) => ({
        value,
        label: modelDisplayName(config, value),
        title: `${modelDisplayName(config, value)} · ${resolveModelChannel(config, value).name}`,
    }));
    return (
        <><Select
            className={`canvas-assistant-model${hasReasoning ? " canvas-assistant-model-with-reasoning" : ""}`}
            aria-label="助手模型"
            size="small"
            variant="borderless"
            value={selected || undefined}
            placeholder={options.length ? "选择助手模型" : "暂无可用模型"}
            options={options}
            disabled={busy || options.length === 0}
            placement="topLeft"
            popupMatchSelectWidth={false}
            onChange={(value) => {
                if (!busy && assistantModelOptions(useConfigStore.getState().config).includes(value)) {
                    useConfigStore.getState().updateConfig("assistantModel", value);
                }
            }}
        />
        <ModelReasoningPicker config={config} modelKey={selected} disabled={busy} /></>
    );
}
