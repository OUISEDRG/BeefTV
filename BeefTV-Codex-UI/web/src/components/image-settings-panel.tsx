import { ImageSizePicker } from "./image-size-picker";
import { imageResolutionUsesQuality } from "@/lib/image-size-presets";
import { useEffect, useState, type ReactNode } from "react";
import { ConfigProvider } from "antd";
import { Switch } from "@/components/ui/base/switch";

import { type CanvasTheme } from "@/lib/canvas-theme";
import { buildImageResolutionOptions, formatImageResolutionSize } from "@/lib/image-resolution-tiers";
import { modelCapabilityConfigFor, normalizeImageValue, type ImageCapabilityConfig } from "@/lib/model-capabilities";
import { mergedImageCapabilityConfig } from "@/lib/model-selection";
import { modelOptionName, resolveModelChannel, type AiConfig } from "@/stores/use-config-store";
import { seedreamFamily } from "@/lib/seedream-image";
import { SeedreamSettings } from "./seedream-settings";
import { declaredGenerationConfigError } from "@/lib/declared-generation-config";
import { canvasImageGenerationCountOptions, resolveCanvasImageGenerationCount } from "@/lib/canvas/canvas-image-generation-count";

const qualityOptions = [
    { value: "auto", label: "自动" },
    { value: "high", label: "高" },
    { value: "xhigh", label: "超高" },
    { value: "max", label: "最高" },
    { value: "medium", label: "中" },
    { value: "low", label: "低" },
    { value: "1k", label: "1K" },
    { value: "2k", label: "2K" },
    { value: "4k", label: "4K" },
];

type AspectOption = { value: string; label: string; width: number; height: number; icon: string; size?: string };

const aspectOptions: AspectOption[] = [
    { value: "1:1", label: "1:1", width: 1024, height: 1024, icon: "square" },
    { value: "3:2", label: "3:2", width: 1536, height: 1024, icon: "landscape" },
    { value: "2:3", label: "2:3", width: 1024, height: 1536, icon: "portrait" },
    { value: "4:3", label: "4:3", width: 1360, height: 1024, icon: "landscape" },
    { value: "3:4", label: "3:4", width: 1024, height: 1360, icon: "portrait" },
    { value: "16:9", label: "16:9", width: 1824, height: 1024, icon: "landscape" },
    { value: "2:1", label: "2:1", size: "2048x1024", width: 2048, height: 1024, icon: "landscape" },
    { value: "1:2", label: "1:2", size: "1024x2048", width: 1024, height: 2048, icon: "portrait" },
    { value: "21:9", label: "21:9", size: "2352x1008", width: 2352, height: 1008, icon: "landscape" },
    { value: "9:16", label: "9:16", width: 1024, height: 1824, icon: "portrait" },
    { value: "1:1-2k", label: "1:1(2k)", size: "2048x2048", width: 2048, height: 2048, icon: "square" },
    { value: "16:9-2k", label: "16:9(2k)", size: "2048x1152", width: 2048, height: 1152, icon: "landscape" },
    { value: "9:16-2k", label: "9:16(2k)", size: "1152x2048", width: 1152, height: 2048, icon: "portrait" },
    { value: "16:9-4k", label: "16:9(4k)", size: "3840x2160", width: 3840, height: 2160, icon: "landscape" },
    { value: "9:16-4k", label: "9:16(4k)", size: "2160x3840", width: 2160, height: 3840, icon: "portrait" },
    { value: "auto", label: "auto", width: 0, height: 0, icon: "auto" },
];

type ImageSettingsPanelProps = {
    config: AiConfig;
    onConfigChange: (key: "quality" | "size" | "transparentBackground" | "count" | "seedreamMode" | "seedreamMaxImages" | "seedreamOutputFormat" | "seedreamOptimize" | "seedreamWatermark", value: string) => void;
    theme: CanvasTheme;
    showTitle?: boolean;
    showQuality?: boolean;
    showTransparent?: boolean;
    showSize?: boolean;
    showCount?: boolean;
    className?: string;
    maxCount?: number;
};

export function ImageSettingsPanel({ config, onConfigChange, theme, showTitle = true, showQuality = true, showTransparent = true, showSize = true, showCount = true, className = "w-[304px] space-y-3 rounded-2xl px-1 py-0.5", maxCount = 15 }: ImageSettingsPanelProps) {
    const profile = mergedImageCapabilityConfig(config, config.model || config.imageModel);
    const selectedModel = config.model || config.imageModel;
    const declaredResolution = Boolean(resolveModelChannel(config, selectedModel).modelProfiles?.find((item) => item.model === modelOptionName(selectedModel))?.imageCapabilitiesVersion?.trim());
    const seedream = seedreamFamily(config.model || config.imageModel);
    const normalized = normalizeImageValue(profile, config);
    const quality = declaredResolution ? config.quality || normalized.quality : normalized.quality;
    const transparentBackground = normalized.transparentBackground === "true";
    const batchCounts = canvasImageGenerationCountOptions(profile, maxCount);
    const effectiveMaxCount = batchCounts[batchCounts.length - 1];
    const count = resolveCanvasImageGenerationCount(profile, config.count, maxCount);
    const activeSize = declaredResolution ? config.size || normalized.size : normalized.size;
    const parameterError = declaredGenerationConfigError(config, "image");
    const activeQualityOptions = profile.quality.values.map((value) => qualityOptions.find((item) => item.value === value) || { value, label: value });

    return (
        <ImageSettingsTheme theme={theme}>
            <div
                className={className}
                style={{ color: theme.node.text }}
                onMouseDown={(event) => {
                    event.stopPropagation();
                    if (event.target instanceof HTMLInputElement) return;
                    if (document.activeElement instanceof HTMLInputElement && event.currentTarget.contains(document.activeElement)) document.activeElement.blur();
                }}
            >
                {showTitle ? <div className="text-base font-semibold">图像设置</div> : null}
                {parameterError ? <p role="alert" className="text-xs">{parameterError}</p> : null}
                {showQuality && profile.quality.supported && !imageResolutionUsesQuality(profile) ? <div className="space-y-2">
                    <SettingTitle color={theme.node.muted}>{isGrokResolutionQuality(profile) ? "分辨率" : "质量"}</SettingTitle>
                    <div className={`grid gap-1.5 ${activeQualityOptions.length <= 2 ? "grid-cols-2" : "grid-cols-4"}`}>
						{activeQualityOptions.map((item) => (
                            <OptionPill key={item.value} selected={quality === item.value} theme={theme} onClick={() => onConfigChange("quality", item.value)}>
                                {item.label}
                            </OptionPill>
                        ))}
                    </div>
                </div> : null}
                {showTransparent && profile.transparentBackground.supported && (!seedream || config.seedreamMode !== "layers") ? <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                        <SettingTitle color={theme.node.muted}>透明背景</SettingTitle>
                        <div className="mt-1 text-[var(--fs-label)]" style={{ color: theme.node.muted }}>
                            {seedream ? "保留参考图中的透明区域，需要连接一张透明图片。" : "请求模型输出保留 Alpha 通道的 PNG"}
                        </div>
                    </div>
                    <span title="是否支持透明背景由当前模型接口决定" onMouseDown={(event) => event.stopPropagation()}>
                        <Switch
                            size="sm"
                            checked={transparentBackground}
                            onChange={(checked) => { onConfigChange("transparentBackground", checked ? "true" : "false"); if (checked && seedream) onConfigChange("seedreamOutputFormat", "png"); }}
                        />
                    </span>
                </div> : null}
                {seedream ? <SeedreamSettings config={config} family={seedream} profile={profile} onChange={onConfigChange} theme={theme} /> : showSize ? <ImageSizePicker profile={profile} size={activeSize} quality={quality} declaredResolution={declaredResolution} onChange={(size, nextQuality) => applyImageSizeSelection(onConfigChange, size, nextQuality)} /> : null}
                {showCount && profile.fixedOutputs && profile.fixedOutputs > 1 ? (
                    <div className="space-y-2">
                        <SettingTitle color={theme.node.muted}>{seedream ? "生成批次" : "生成张数"}</SettingTitle>
                        <div className="grid grid-cols-3 gap-1.5">
                            {batchCounts.map((value) => <OptionPill key={value} selected={count === value} theme={theme} onClick={() => onConfigChange("count", String(value))}>{value}</OptionPill>)}
                        </div>
                        <div className="text-[var(--fs-label)]" style={{ color: theme.node.muted }}>每批 {profile.fixedOutputs} 张，共 {count / profile.fixedOutputs} 批；按实际批次计费</div>
                    </div>
                ) : showCount && effectiveMaxCount > 1 ? (
                    <div className="space-y-2">
                        <SettingTitle color={theme.node.muted}>{seedream ? "生成批次" : "生成张数"}</SettingTitle>
                        <CountInput value={count} max={effectiveMaxCount} theme={theme} onChange={(value) => onConfigChange("count", String(value))} />
                        <div className="text-[var(--fs-label)]" style={{ color: theme.node.muted }}>{seedream ? `可选 1–${effectiveMaxCount} 批` : `可选 1–${effectiveMaxCount} 张，结果保存为历史版本`}</div>
                    </div>
                ) : null}
            </div>
        </ImageSettingsTheme>
    );
}

export function applyImageSizeSelection(onConfigChange: ImageSettingsPanelProps["onConfigChange"], size: string, quality?: string) {
    onConfigChange("size", size);
    if (quality) onConfigChange("quality", quality);
}

export function ImageSettingsTheme({ theme, children }: { theme: CanvasTheme; children: ReactNode }) {
    return (
        <ConfigProvider
            theme={{
                token: { colorBgContainer: theme.canvas.background, colorBgElevated: theme.canvas.background, colorBorder: theme.node.stroke, colorPrimary: theme.node.activeStroke, colorText: theme.node.text, colorTextLightSolid: theme.node.panel },
                components: { Button: { defaultBg: theme.canvas.background, defaultBorderColor: theme.node.stroke, defaultColor: theme.node.text } },
            }}
        >
            {children}
        </ConfigProvider>
    );
}

export function imageQualityLabel(value: string) {
    return ({ auto: "自动", high: "高", xhigh: "超高", max: "最高", medium: "中", low: "低", "1k": "1K", "2k": "2K", "4k": "4K" } as Record<string, string>)[value.toLowerCase()] || value || "默认";
}

function isGrokResolutionQuality(profile: ImageCapabilityConfig) {
    const values = profile.quality.values.map((item) => item.toLowerCase());
    return values.some((value) => ["1k", "2k", "4k"].includes(value));
}

export function imageSizeLabel(size: string, profile?: ImageCapabilityConfig) {
    const resolutionLabel = formatImageResolutionSize(size, profile?.size.presets || buildImageResolutionOptions([size]));
    return resolutionLabel !== size ? resolutionLabel : aspectOptions.find((item) => (item.size || item.value) === size || item.value === size)?.label || size;
}

function OptionPill({ selected, disabled = false, theme, onClick, children }: { selected: boolean; disabled?: boolean; theme: CanvasTheme; onClick: () => void; children: ReactNode }) {
    return (
        <button
            aria-pressed={selected}
            type="button"
			className="h-8 cursor-pointer rounded-full px-2 text-xs transition-colors hover:brightness-110 focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-1 disabled:cursor-not-allowed disabled:opacity-40"
			style={{ background: selected ? theme.toolbar.activeBg : "transparent", color: theme.node.text, outlineColor: theme.node.muted }}
			disabled={disabled}
            onMouseDown={(event) => event.stopPropagation()}
            onClick={onClick}
        >
            {children}
        </button>
    );
}

function CountInput({ value, max, theme, onChange }: { value: number; max: number; theme: CanvasTheme; onChange: (value: number) => void }) {
    const [draft, setDraft] = useState(String(value));
    useEffect(() => setDraft(String(value)), [value]);
    const parsed = Number(draft);
    const current = Math.max(1, Math.min(max, Number.isFinite(parsed) ? Math.floor(parsed) : value));
    const commit = (next = current) => {
        const bounded = Math.max(1, Math.min(max, next));
        setDraft(String(bounded));
        if (bounded !== value) onChange(bounded);
    };
    const buttonClassName = "h-8 w-9 shrink-0 rounded-full text-base hover:brightness-110 focus-visible:outline focus-visible:outline-1 disabled:cursor-not-allowed disabled:opacity-40";
    return (
        <div className="flex h-8 w-[168px] items-center overflow-hidden rounded-full text-xs" data-canvas-no-zoom
            style={{ background: theme.toolbar.itemHover, color: theme.node.text }} onKeyDown={event => event.stopPropagation()} onWheel={event => event.stopPropagation()}>
            <button type="button" aria-label="减少生成张数" className={buttonClassName} disabled={current <= 1}
                onMouseDown={event => event.preventDefault()} onClick={() => commit(current - 1)}>−</button>
            <input
                type="number"
                min={1}
                max={max}
                step={1}
                aria-label="自定义生成张数"
                className="min-w-0 flex-1 bg-transparent px-2 text-center outline-none focus-visible:underline [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                style={{ color: theme.node.text, WebkitTextFillColor: theme.node.text }}
                value={draft}
                onChange={event => setDraft(event.target.value)}
                onBlur={() => commit()}
                onKeyDown={(event) => {
                    if (event.key === "Enter") event.currentTarget.blur();
                    if (event.key === "ArrowUp" || event.key === "ArrowDown") {
                        event.preventDefault();
                        commit(current + (event.key === "ArrowUp" ? 1 : -1));
                    }
                }}
                onMouseDown={(event) => event.stopPropagation()}
            />
            <button type="button" aria-label="增加生成张数" className={buttonClassName} disabled={current >= max}
                onMouseDown={event => event.preventDefault()} onClick={() => commit(current + 1)}>＋</button>
        </div>
    );
}

function SettingTitle({ children, color }: { children: string; color: string }) {
    return (
        <div className="text-xs font-medium" style={{ color }}>
            {children}
        </div>
    );
}
