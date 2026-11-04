import type { DreamaVoiceBinding, DreamaVoiceCatalogItem } from "@/types/dreama-voice";
import type { AiConfig, ModelChannel } from "@/stores/use-config-store";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";

function record(value: unknown): value is Record<string, unknown> { return Boolean(value && typeof value === "object" && !Array.isArray(value)); }
function identifier(value: unknown): value is string { return typeof value === "string" && /^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,159}$/.test(value); }

export function parseDreamaVoiceBinding(value: unknown): DreamaVoiceBinding | undefined {
    if (!record(value) || value.version !== 1 || typeof value.name !== "string" || !value.name.trim() || value.name.length > 160) return undefined;
    if (value.source === "catalog" && identifier(value.voiceId) && value.sampleResourceId === undefined) {
        return { version: 1, name: value.name, source: "catalog", voiceId: value.voiceId };
    }
    if (value.source === "resource" && identifier(value.sampleResourceId) && value.voiceId === undefined) {
        return { version: 1, name: value.name, source: "resource", sampleResourceId: value.sampleResourceId };
    }
    return undefined;
}

export function updateImageVoiceBinding(nodes: CanvasNodeData[], nodeId: string, binding: DreamaVoiceBinding | undefined): CanvasNodeData[] {
    const target = nodes.find(node => node.id === nodeId);
    if (!target || target.type !== CanvasNodeType.Image) throw new Error("原图片节点已不存在，请重新选择图片");
    const parsed = parseDreamaVoiceBinding(binding);
    if (binding !== undefined && !parsed) throw new Error("音色绑定信息不完整，请重新选择");
    return nodes.map(node => {
        if (node.id !== nodeId) return node;
        const metadata = { ...node.metadata };
        if (parsed) metadata.dreamaVoiceBinding = parsed;
        else delete metadata.dreamaVoiceBinding;
        return { ...node, metadata };
    });
}

export function parseDreamaVoiceCatalog(value: unknown): DreamaVoiceCatalogItem[] {
    if (!record(value) || !Array.isArray(value.voices)) throw new Error("剧梦音色目录格式不正确，请刷新后重试");
    const seen = new Set<string>();
    return value.voices.map(row => {
        if (!record(row) || !identifier(row.id) || typeof row.name !== "string" || !row.name.trim() || row.name.length > 160 || !["public", "personal"].includes(String(row.source)) || seen.has(row.id)) throw new Error("剧梦音色目录包含无效或重复音色，请刷新后重试");
        seen.add(row.id);
        const previewUrl = typeof row.previewUrl === "string" ? row.previewUrl : "";
        if (previewUrl) {
            let url: URL;
            try { url = new URL(previewUrl); } catch { throw new Error("音色试听地址无效"); }
            if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("音色试听地址无效");
        }
        return { id: row.id, name: row.name, source: row.source as "public" | "personal", previewUrl, ...(typeof row.description === "string" ? { description: row.description.slice(0, 500) } : {}) };
    });
}

export function filterDreamaVoices(items: DreamaVoiceCatalogItem[], query: string, source: "all" | "public" | "personal"): DreamaVoiceCatalogItem[] {
    const keyword = query.trim().toLocaleLowerCase();
    return items.filter(item => (source === "all" || item.source === source) && (!keyword || [item.name, item.description, item.id].filter(Boolean).join(" ").toLocaleLowerCase().includes(keyword)));
}

export function dreamaVoiceCatalogChannel(config: AiConfig): ModelChannel | undefined {
    return config.channels.find(channel => {
        if (!channel.enabled || !(channel.interfaceType === "dreama-audio-async" || channel.modelProfiles?.some(profile => profile.protocol === "dreama-audio-async" && profile.capability === "audio"))) return false;
        try {
            const url = new URL(channel.baseUrl);
            return ["http:", "https:"].includes(url.protocol) && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname) && !url.username && !url.password && !url.search && !url.hash;
        } catch { return false; }
    });
}
