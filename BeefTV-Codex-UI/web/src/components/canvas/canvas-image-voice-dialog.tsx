import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, Input, Segmented, Spin } from "antd";
import { Check, FolderOpen, Play, RefreshCw, Volume2 } from "lucide-react";
import { AppModal } from "@/components/ui/product/app-modal";
import { AssetLibraryPickerModal, type AssetLibraryPickerItem } from "@/components/assets/asset-library-picker-modal";
import { CHARACTER_VOICE_FORMAT_LABEL, CHARACTER_VOICE_UPLOAD_ACCEPT, characterVoiceTitleFromFileName, isSupportedCharacterVoiceFile } from "@/lib/character-voice-formats";
import { dreamaVoiceCatalogChannel, filterDreamaVoices, parseDreamaVoiceBinding } from "@/lib/dreama-voice";
import { listDreamaVoices } from "@/services/api/dreama-voices";
import { getResource, resourceIdFromStorageKey } from "@/services/api/resources";
import { resolveDreamaVoicePreview } from "@/services/dreama-voice-preview";
import { uploadMediaFile } from "@/services/file-storage";
import { useAssetStore, type Asset } from "@/stores/use-asset-store";
import type { AiConfig } from "@/stores/use-config-store";
import type { CanvasNodeData } from "@/types/canvas";
import type { DreamaVoiceBinding, DreamaVoiceCatalogItem } from "@/types/dreama-voice";

export function DreamaVoiceBindingButton({ binding, onClick }: { binding?: DreamaVoiceBinding; onClick: () => void }) {
    return <button type="button" className="aceternity-dock-command is-labeled pointer-events-auto inline-flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-[var(--dock-item-radius)] px-2.5 outline-none" aria-label={binding ? `绑定音色：${binding.name}` : "绑定音色"} aria-pressed={Boolean(binding)} title={binding ? `当前音色：${binding.name}，点击修改` : "为图片中的人物绑定剧梦音色"} onClick={onClick}>
        <Volume2 className="size-3.5 shrink-0" /><span className="max-w-36 truncate text-[var(--fs-label)] font-medium">{binding ? `音色 · ${binding.name}` : "绑定音色"}</span>
    </button>;
}

export function DreamaVoiceChoices({ voices, selectedId, onSelect, onPreview }: { voices: DreamaVoiceCatalogItem[]; selectedId?: string; onSelect: (voice: DreamaVoiceCatalogItem) => void; onPreview: (voice: DreamaVoiceCatalogItem) => void }) {
    return <div className="divide-y divide-border" role="group" aria-label="剧梦音色列表">
        {voices.map(voice => <div key={voice.id} className="flex items-center gap-2 py-2">
            <button type="button" className="flex min-w-0 flex-1 items-center gap-3 rounded-md p-2 text-left hover:bg-surface-hover focus-visible:outline" aria-pressed={selectedId === voice.id} onClick={() => onSelect(voice)}>
                <span className="grid size-7 shrink-0 place-items-center rounded-full border border-border">{selectedId === voice.id ? <Check className="size-4" /> : <Volume2 className="size-3.5 text-foreground/45" />}</span>
                <span className="min-w-0"><span className="block truncate text-sm font-medium">{voice.name}</span><span className="block truncate text-xs text-foreground/50">{voice.source === "personal" ? "我的音色" : "公共音色"}{voice.description ? ` · ${voice.description}` : ""}</span></span>
            </button>
            <button type="button" className="inline-flex h-8 shrink-0 items-center gap-1 rounded-md px-2 text-xs hover:bg-surface-hover disabled:opacity-35" aria-label={`试听 ${voice.name}`} disabled={!voice.previewUrl} onClick={() => onPreview(voice)}><Play className="size-3.5" />试听</button>
        </div>)}
    </div>;
}

type Props = { node: CanvasNodeData; config: AiConfig; assets: Asset[]; onClose: () => void; onSave: (binding: DreamaVoiceBinding | undefined) => void | Promise<void> };

export function CanvasImageVoiceDialog({ node, config, assets, onClose, onSave }: Props) {
    const [draft, setDraft] = useState(() => parseDreamaVoiceBinding(node.metadata?.dreamaVoiceBinding));
    const [keyword, setKeyword] = useState("");
    const [source, setSource] = useState<"all" | "public" | "personal">("all");
    const [pickerOpen, setPickerOpen] = useState(false);
    const [preview, setPreview] = useState<{ name: string; url: string } | null>(null);
    const previewRequest = useRef(0);
    const [previewLoading, setPreviewLoading] = useState(false);
    const [error, setError] = useState("");
    const [saving, setSaving] = useState(false);
    const localAssets = useAssetStore(state => state.assets);
    const addAsset = useAssetStore(state => state.addAsset);
    const channel = dreamaVoiceCatalogChannel(config);
    const catalog = useQuery({ queryKey: ["dreama-voice-catalog", channel?.id, channel?.baseUrl], queryFn: ({ signal }) => listDreamaVoices(config, signal), staleTime: 30_000, retry: false });
    const voices = filterDreamaVoices(catalog.data || [], keyword, source);
    const audioAssets = useMemo(() => [...new Map([...assets, ...localAssets].filter(asset => asset.kind === "audio" && asset.status !== "archived").map(asset => [asset.id, asset])).values()], [assets, localAssets]);
    const audioItems: AssetLibraryPickerItem[] = audioAssets.map(asset => ({ id: asset.id, title: asset.title, category: "audio", kindLabel: "本机音频", asset, disabledReason: asset.kind !== "audio" || !resourceIdFromStorageKey(asset.data.storageKey) ? "声音尚未保存到 BeefTV 本机资源库" : undefined }));

    useEffect(() => {
        setDraft(parseDreamaVoiceBinding(node.metadata?.dreamaVoiceBinding)); setPreview(null); setError("");
        return () => { previewRequest.current++; };
    }, [node.id]);

    const stopPreview = () => { previewRequest.current++; setPreview(null); setPreviewLoading(false); };
    const previewLocal = async (resourceId: string, name: string) => {
        const request = ++previewRequest.current;
        setPreview(null); setPreviewLoading(true); setError("");
        try {
            const url = await resolveDreamaVoicePreview(resourceId);
            if (request === previewRequest.current) setPreview({ name, url });
        } catch (cause) {
            if (request === previewRequest.current) setError(cause instanceof Error ? cause.message : "本机声音试听失败，请重新选择声音后重试");
        } finally {
            if (request === previewRequest.current) setPreviewLoading(false);
        }
    };

    const save = async (binding: DreamaVoiceBinding | undefined) => {
        if (saving) return;
        setSaving(true); setError("");
        try { await onSave(binding); onClose(); }
        catch (cause) { setError(cause instanceof Error ? cause.message : "音色绑定未保存，请重试"); }
        finally { setSaving(false); }
    };

    return <>
        <AppModal open title={`绑定音色 · ${node.title || "图片"}`} width={600} onCancel={() => { if (!saving) onClose(); }} maskClosable={!saving} footer={<div className="flex items-center justify-between gap-2">
            <Button danger type="text" disabled={saving || !node.metadata?.dreamaVoiceBinding} onClick={() => void save(undefined)}>解除绑定</Button>
            <div className="flex gap-2"><Button disabled={saving} onClick={onClose}>取消</Button><Button type="primary" disabled={!draft} loading={saving} onClick={() => void save(draft)}>保存绑定</Button></div>
        </div>}>
            <div data-canvas-no-zoom data-canvas-wheel-scroll className="space-y-3" onPointerDown={event => event.stopPropagation()}>
                <p className="text-xs leading-5 text-foreground/55">为这张图片中的人物选择声音。连接到剧梦视频或配音节点时使用；提示词保持原文。</p>
                <div className="flex items-center justify-between gap-2 rounded-md border border-border bg-surface-hover px-3 py-2">
                    <div className="min-w-0"><div className="text-xs text-foreground/50">当前选择</div><div className="truncate text-sm font-medium">{draft?.name || "尚未选择音色"}</div></div>
                    {draft?.source === "resource" && draft.sampleResourceId ? <Button size="small" loading={previewLoading} icon={<Play className="size-3" />} onClick={() => void previewLocal(draft.sampleResourceId!, draft.name)}>试听</Button> : null}
                </div>
                <div className="flex items-center gap-2"><Input.Search value={keyword} onChange={event => setKeyword(event.target.value)} placeholder="搜索音色名称" allowClear /><Button aria-label="刷新剧梦音色" icon={<RefreshCw className="size-4" />} loading={catalog.isFetching} onClick={() => void catalog.refetch()} /></div>
                <Segmented block value={source} onChange={value => setSource(value as typeof source)} options={[{ label: "全部音色", value: "all" }, { label: "公共音色", value: "public" }, { label: "我的音色", value: "personal" }]} />
                <div className="thin-scrollbar max-h-72 min-h-28 overflow-y-auto rounded-md border border-border px-2" data-canvas-wheel-scroll>
                    {catalog.isPending ? <div className="grid min-h-28 place-items-center"><Spin description="正在读取音色" /></div> : catalog.isError ? <p role="alert" className="p-3 text-sm text-warning">{catalog.error instanceof Error ? catalog.error.message : "音色读取失败，请刷新重试"}</p> : !voices.length ? <p className="p-5 text-center text-sm text-foreground/50">{keyword ? "未找到匹配音色" : source === "personal" ? "暂无个人音色，可以选择公共音色或本机声音" : "暂无可用音色"}</p> : <DreamaVoiceChoices voices={voices} selectedId={draft?.source === "catalog" ? draft.voiceId : undefined} onSelect={voice => { setDraft({ version: 1, name: voice.name, source: "catalog", voiceId: voice.id }); stopPreview(); setError(""); }} onPreview={voice => { stopPreview(); setError(""); setPreview({ name: voice.name, url: voice.previewUrl }); }} />}
                </div>
                <Button block icon={<FolderOpen className="size-4" />} onClick={() => setPickerOpen(true)}>选择本机声音或上传音频</Button>
                <p className="text-xs leading-5 text-foreground/45">本机声音在你点击生成时才上传到剧梦。浏览、试听和保存绑定不会创建生成任务。</p>
                {preview ? <div className="rounded-md border border-border p-3"><div className="mb-2 truncate text-xs text-foreground/60">试听 · {preview.name}</div><audio key={preview.url} className="w-full" controls autoPlay preload="none" src={preview.url} onError={() => setError("试听暂时无法播放，可以刷新目录或重新选择声音")} /></div> : null}
                {error ? <p role="alert" className="text-sm text-warning">{error}</p> : null}
            </div>
        </AppModal>
        <AssetLibraryPickerModal open={pickerOpen} items={audioItems} categoryLabels={{ all: "全部音频", audio: "本机声音" }} initialCategory="audio" mediaKinds={["audio"]} multiple={false} title="选择本机声音" eyebrow="人物音色" confirmLabel={() => "使用这份声音"} emptyTitle="还没有本机声音" emptyDescription="上传一段清晰的人声样本，或选择素材库中的音频。" onClose={() => setPickerOpen(false)} onConfirm={async ids => {
            const asset = audioAssets.find(item => item.id === ids[0]) || useAssetStore.getState().assets.find(item => item.id === ids[0]);
            const resourceId = asset?.kind === "audio" ? resourceIdFromStorageKey(asset.data.storageKey) : "";
            if (!asset || !resourceId) throw new Error("请选择已保存到 BeefTV 本机资源库的音频");
            const resource = await getResource(resourceId);
            if (resource.kind !== "audio" || resource.status !== "ready") throw new Error("声音资源尚未就绪，请等待上传完成");
            setDraft({ version: 1, name: asset.title.slice(0, 160) || "本机声音", source: "resource", sampleResourceId: resourceId });
            stopPreview(); setPickerOpen(false); setError("");
        }} upload={{ accept: CHARACTER_VOICE_UPLOAD_ACCEPT, description: `支持 ${CHARACTER_VOICE_FORMAT_LABEL}，保存到本机素材库`, onUpload: async files => {
            const ids: string[] = [];
            for (const file of Array.from(files)) {
                if (!isSupportedCharacterVoiceFile(file)) throw new Error(`声音格式需为 ${CHARACTER_VOICE_FORMAT_LABEL}`);
                const uploaded = await uploadMediaFile(file, "audio");
                if (!resourceIdFromStorageKey(uploaded.storageKey)) throw new Error("声音尚未保存到 BeefTV 本机资源库，请确认本机服务可用后重试");
                ids.push(addAsset({ kind: "audio", title: characterVoiceTitleFromFileName(file.name), coverUrl: "", tags: ["人物音色"], status: "confirmed", source: "图片音色绑定", data: { url: uploaded.url, storageKey: uploaded.storageKey, durationMs: uploaded.durationMs, bytes: uploaded.bytes, mimeType: uploaded.mimeType } }));
            }
            return ids;
        } }} />
    </>;
}
