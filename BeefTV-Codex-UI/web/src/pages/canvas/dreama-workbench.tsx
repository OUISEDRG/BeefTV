import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import { App, Button, Input, Select, Tag } from "antd";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { dreamaApi, type DreamaTask } from "@/services/api/dreama-workbench";
import { bindDreamaProject, dreamaTaskException, dreamaWorkbenchChannel, withoutDreamaBinding } from "@/lib/canvas/dreama-workbench";
import { isFrameNode } from "@/lib/canvas/canvas-frame";
import { persistCanvasDocument } from "@/services/local-workspace-repository";
import { commitModelConfig, flushModelConfig, getModelConfigPersistenceState } from "@/services/model-config-repository";
import { getLocalModelConfig } from "@/services/api/workspace";
import { useConfigStore, type AiConfig } from "@/stores/use-config-store";
import { useCanvasStore, type CanvasProject } from "@/stores/canvas/use-canvas-store";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";
import type { DreamaGenerationPreview } from "@/types/dreama-workbench";
import type { CanvasNodeGenerationOptions } from "./use-canvas-generation-executor";
import type { CanvasNodeGenerationMode } from "@/components/canvas/canvas-node-prompt-panel";
import "./dreama-workbench.css";

type Props = {
    project: CanvasProject;
    config: AiConfig;
    nodes: CanvasNodeData[];
    nodesRef: { current: CanvasNodeData[] };
    selectedNodeIds: Set<string>;
    setNodes: Dispatch<SetStateAction<CanvasNodeData[]>>;
    readOnly: boolean;
    saveCanvas: () => Promise<boolean>;
    generate: (nodeId: string, mode: CanvasNodeGenerationMode, prompt: string, options?: CanvasNodeGenerationOptions) => unknown;
    focusNode: (nodeId: string) => void;
    openTask: (nodeId: string) => void;
};

export function DreamaWorkbench(props: Props) {
    const { project, config, nodes, nodesRef, selectedNodeIds, setNodes, readOnly, saveCanvas, generate, focusNode, openTask } = props;
    const { message } = App.useApp();
    const navigate = useNavigate();
    const queries = useQueryClient();
    const channel = useMemo(() => dreamaWorkbenchChannel(config), [config]);
    const binding = project.integrations?.dreama;
    const [expanded, setExpanded] = useState(false);
    const [pendingProjectId, setPendingProjectId] = useState<number>();
    const [newSeriesName, setNewSeriesName] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const channelId = channel?.id || "";
    const projectId = binding?.projectId ?? pendingProjectId;
    useEffect(() => { setPendingProjectId(undefined); setNewSeriesName(""); setError(""); }, [project.id]);

    const directory = useQuery({
        queryKey: ["dreama-projects", channelId], enabled: Boolean(channelId), staleTime: 30_000,
        queryFn: async ({ signal }) => { await dreamaApi.ensure(channelId, signal); return dreamaApi.projects(channelId, signal); },
        retry: false,
    });
    const series = useQuery({
        queryKey: ["dreama-series", channelId, projectId], enabled: Boolean(channelId && projectId && directory.isSuccess),
        queryFn: ({ signal }) => dreamaApi.series(channelId, projectId!, signal), retry: false,
    });
    const tasks = useQuery({
        queryKey: ["dreama-task-exceptions", channelId, project.id], enabled: Boolean(expanded && channelId && directory.isSuccess),
        queryFn: ({ signal }) => dreamaApi.taskExceptions(channelId, project.id, signal), retry: false,
    });
    const selected = nodes.filter(node => selectedNodeIds.has(node.id));
    const selectedGroup = selected.length === 1 && isFrameNode(selected[0]) ? selected[0] : undefined;
    const previewNode = selected.length === 1 ? selected[0] : undefined;
    const preview = previewNode?.metadata?.dreamaGenerationPreview;
    const groups = nodes.filter(isFrameNode);
    const seriesOptions = series.data?.items.map(item => ({ value: item.id, label: item.name })) || [];
    const exceptions = tasks.data?.items.filter(task => dreamaTaskException(task)) || [];

    if (!channel && !binding) return null;
    const run = async (operation: () => Promise<unknown>) => {
        setBusy(true); setError("");
        try { await operation(); } catch (cause) { const detail = cause instanceof Error ? cause.message : "剧梦操作失败"; setError(detail); message.error(detail); }
        finally { setBusy(false); }
    };
    const saveBinding = async (defaultSeriesId: number) => {
        const selectedProject = directory.data?.items.find(item => item.id === projectId);
        if (!selectedProject || !series.data?.items.some(item => item.id === defaultSeriesId && item.projectId === selectedProject.id)) throw new Error("项目或剧集目录已变化，请刷新后重新选择");
        await persistCanvasDocument(project.id, { integrations: { ...project.integrations, dreama: bindDreamaProject(binding, selectedProject, defaultSeriesId) } });
    };
    const saveGroup = async (groupId: string, seriesId: number | undefined) => {
        if (!binding) throw new Error("请先设置画布项目与默认剧集");
        if (seriesId && !series.data?.items.some(item => item.id === seriesId && item.projectId === binding.projectId)) throw new Error("该剧集不属于当前项目");
        const next = nodesRef.current.map(node => node.id === groupId ? { ...node, metadata: { ...node.metadata, dreamaSeriesId: seriesId } } : node);
        setNodes(next);
        await persistCanvasDocument(project.id, { nodes: next, integrations: { ...project.integrations, dreama: { ...binding, revision: binding.revision + 1 } } });
    };
    const refreshCatalog = async () => {
        await flushModelConfig();
        if (getModelConfigPersistenceState().dirty) throw new Error("模型设置尚未保存，请先处理保存错误");
        const result = await dreamaApi.refreshCatalog(channelId, getModelConfigPersistenceState().revision);
        const latest = await getLocalModelConfig();
        const current = useConfigStore.getState().config;
        // Only adopt refreshed catalog fields. Edits to unrelated settings remain local.
        const next = { ...current, channels: current.channels.map(item => {
            const refreshed = result.updatedChannels.includes(item.id) ? latest.config.channels.find(value => value.id === item.id) : undefined;
            return refreshed ? { ...item, models: refreshed.models, modelProfiles: refreshed.modelProfiles } : item;
        }) };
        useConfigStore.getState().replaceConfig(next);
        await commitModelConfig(next);
        await flushModelConfig();
        if (getModelConfigPersistenceState().dirty) throw new Error("目录已更新，当前模型设置尚未保存，请重试保存");
        message.success("剧梦模型目录已刷新");
    };
    const copyForBinding = async () => {
        if (!await saveCanvas()) throw new Error("请先完成当前画布保存");
        const source = useCanvasStore.getState().openProject(project.id);
        if (!source) throw new Error("当前画布已不存在");
        const copied = withoutDreamaBinding(source);
        const id = useCanvasStore.getState().importProject({ ...copied, title: `${source.title} 副本`, revision: 0, remoteContentHash: undefined });
        await persistCanvasDocument(id, { integrations: copied.integrations });
        navigate(`/canvas/${id}`);
    };
    const requestPreview = async () => {
        if (!previewNode) return;
        const mode = previewNode.type === CanvasNodeType.Video ? "video" : previewNode.type === CanvasNodeType.Audio ? "audio" : "image";
        await generate(previewNode.id, mode, previewNode.metadata?.composerContent ?? previewNode.metadata?.prompt ?? "", { previewOnly: true, skipDuplicateConfirmation: true });
        setExpanded(true);
    };
    const problem = error || directory.error?.message || series.error?.message || (expanded ? tasks.error?.message : undefined);
    return <section className="dreama-workbench text-foreground" data-canvas-no-zoom data-canvas-wheel-scroll aria-label="剧梦工作台">
        <div className="dreama-workbench-controls flex items-center gap-2 text-xs">
            <strong>剧梦</strong>
            <div className="flex items-center gap-1"><span>项目</span><Select size="small" aria-label="剧梦项目" placeholder="选择剧梦项目" value={projectId} disabled={readOnly || busy || Boolean(binding)} loading={directory.isLoading} style={{ width: 180 }}
                options={directory.data?.items.map(item => ({ value: item.id, label: item.name, disabled: item.promptMode !== "drama" })) ?? (binding ? [{ value: binding.projectId, label: binding.projectName, disabled: false }] : [])}
                onChange={setPendingProjectId} /></div>
            <div className="flex items-center gap-1"><span>默认剧集</span><Select size="small" aria-label="默认剧集" placeholder="选择默认剧集" value={binding?.defaultSeriesId} disabled={readOnly || busy || !projectId || !series.isSuccess} loading={series.isLoading} style={{ width: 150 }} options={seriesOptions} onChange={value => void run(() => saveBinding(value))} /></div>
            {binding ? <span className="text-muted-foreground">{binding.aspectRatio} · 剧本模式</span> : <span className="text-muted-foreground">选择默认剧集后自动保存绑定</span>}
            {selectedGroup && binding ? <Select size="small" allowClear aria-label="选中分组剧集" placeholder="分组使用默认剧集" style={{ width: 165 }} options={seriesOptions} value={selectedGroup.metadata?.dreamaSeriesId} disabled={readOnly || busy} onChange={value => void run(() => saveGroup(selectedGroup.id, value))} /> : null}
            <Button size="small" aria-pressed={expanded} onClick={() => setExpanded(value => !value)}>预检与异常</Button>
            <Button size="small" disabled={busy || !channelId} onClick={() => void run(async () => { await directory.refetch({ throwOnError: true }); if (projectId) await series.refetch({ throwOnError: true }); if (expanded) await tasks.refetch({ throwOnError: true }); })}>刷新</Button>
            <Button size="small" disabled={readOnly || busy || !channelId} onClick={() => void run(refreshCatalog)}>刷新模型目录</Button>
        </div>
        {problem || !channel || expanded ? <div className="dreama-workbench-panel">
        {problem ? <p role="alert" className="m-0 px-3 py-2 text-xs text-destructive">{problem}</p> : null}
        {!channel ? <p className="m-0 px-3 py-2 text-xs">请在模型设置中启用已保存的本机剧梦渠道。</p> : null}
        {expanded ? <div className="grid grid-cols-1 gap-3 p-3 text-xs lg:grid-cols-3">
            <div className="space-y-2">
                <strong>剧集与分组</strong>
                <div className="flex gap-1"><Input size="small" aria-label="新剧集名称" placeholder="输入新剧集名称" value={newSeriesName} onChange={event => setNewSeriesName(event.target.value)} disabled={readOnly || !channelId || !projectId || busy} /><Button size="small" disabled={readOnly || !channelId || !projectId || !newSeriesName.trim() || busy} onClick={() => void run(async () => {
                    await dreamaApi.createSeries(channelId, projectId!, newSeriesName.trim()); setNewSeriesName(""); await queries.invalidateQueries({ queryKey: ["dreama-series", channelId, projectId] });
                })}>创建剧集</Button></div>
                {groups.map(group => <div key={group.id} className="flex items-center gap-2"><button className="min-w-0 flex-1 truncate text-left" onClick={() => focusNode(group.id)}>{group.title} · {nodes.filter(node => node.parentId === group.id).length} 节点</button><Select size="small" aria-label={`${group.title}剧集`} placeholder="默认剧集" allowClear value={group.metadata?.dreamaSeriesId} options={seriesOptions} disabled={readOnly || !binding || busy} style={{ width: 130 }} onChange={value => void run(() => saveGroup(group.id, value))} /></div>)}
                {!groups.length ? <p>在画布中将节点编组后，可为各组选择不同剧集。未分组节点使用默认剧集。</p> : null}
                <p className="text-muted-foreground">人物与场景可以跨集引用。任务按生成节点所属分组冻结剧集。</p>
                {binding ? <Button size="small" disabled={busy || readOnly} onClick={() => void run(copyForBinding)}>复制画布并绑定其他项目</Button> : null}
            </div>
            <div className="space-y-2">
                <div className="flex items-center justify-between"><strong>最终生成快照</strong><Button size="small" disabled={busy || readOnly || !binding || !previewNode || ![CanvasNodeType.Image, CanvasNodeType.Video, CanvasNodeType.Audio, CanvasNodeType.Config].includes(previewNode.type as CanvasNodeType)} onClick={() => void run(requestPreview)}>预览选中节点</Button></div>
                {preview ? <DreamaSnapshotPreview preview={preview} onFocus={focusNode} /> : <p>选中生成节点后预览，显示最终正文、素材顺序、声线与参数。预览不会提交生成。</p>}
                {preview ? <p className="text-muted-foreground">显示最近一次编译快照；点击生成时会按当前输入重新校验。</p> : null}
                {previewNode?.metadata?.generationPreflightError ? <p role="alert">{previewNode.metadata.generationPreflightError}</p> : null}
            </div>
            <div className="space-y-2"><strong>当前画布异常</strong><p className="text-muted-foreground">生成进度与结果请在任务中心查看。</p>{tasks.isLoading ? <p>正在读取异常记录…</p> : !exceptions.length ? <p>暂无需要核对的任务</p> : exceptions.map(task => <DreamaTaskRow key={task.id} task={task} onOpen={() => { focusNode(task.nodeId); openTask(task.nodeId); }} />)}</div>
        </div> : null}
        </div> : null}
    </section>;
}

const parameterNames: Record<string, string> = { size: "比例", quality: "图片分辨率", count: "张数", transparentBackground: "透明背景", videoSeconds: "时长（秒）", vquality: "分辨率", videoGenerateAudio: "生成声音", videoWatermark: "水印", audioVoice: "音色", audioFormat: "格式", audioSpeed: "语速", audioPitch: "音调", audioVolume: "音量" };
const taskNames: Record<string, string> = { image: "图片", video: "视频", extension: "延长", enhancement: "增强", audio: "配音" };

export function DreamaSnapshotPreview({ preview, onFocus }: { preview: DreamaGenerationPreview; onFocus: (id: string) => void }) {
    return <div className="space-y-2">
        <p>项目 {preview.binding.projectId} · 剧集 {preview.binding.seriesId} · 绑定版本 {preview.binding.bindingRevision}</p>
        <p className="break-all">{preview.model}</p>
        <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded border border-border p-2">{preview.prompt}</pre>
        <div className="flex flex-wrap gap-1">{preview.media.map(media => <Button key={`${media.kind}:${media.index}`} size="small" onClick={() => onFocus(media.id)}>@{media.kind}{media.index} · {media.name}</Button>)}</div>
        {preview.voices.map((voice, index) => <p key={index}>@图片{voice.image_index} · {voice.name} · {voice.voice_id || (voice.sample ? "已保存的音频样本" : "")}</p>)}
        <div className="flex flex-wrap gap-1">{Object.entries(preview.parameters).filter(([, value]) => value !== undefined && value !== "").map(([key, value]) => <Tag key={key}>{parameterNames[key] || key}: {typeof value === "boolean" ? (value ? "开启" : "关闭") : String(value)}</Tag>)}</div>
    </div>;
}

export function DreamaTaskRow({ task, onOpen }: { task: DreamaTask; onOpen: () => void }) {
    const label = dreamaTaskException(task);
    if (!label) return null;
    return <div className="space-y-1 rounded border border-border p-2">
        <div className="flex items-center gap-2"><button className="min-w-0 flex-1 truncate text-left" onClick={onOpen}>{taskNames[task.kind] || task.kind} · 剧集 {task.seriesId}</button><Tag>{label}</Tag></div>
        {task.pointsEstimate != null ? <p>预计 {task.pointsEstimate} 积分</p> : null}
        {task.error ? <p role="alert" className="break-words">{task.error}</p> : null}
    </div>;
}
