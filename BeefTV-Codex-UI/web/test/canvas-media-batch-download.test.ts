import { afterEach, expect, test } from "bun:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { CanvasProjectContextMenu } from "@/pages/canvas/canvas-project-context-menu";
import { batchDownloadCanvasMediaNodes } from "@/services/canvas-media-batch-download";
import { CanvasNodeType, type CanvasNodeData, type ContextMenuState } from "@/types/canvas";

const originalWindow = globalThis.window;
const originalFetch = globalThis.fetch;

afterEach(() => {
    globalThis.window = originalWindow;
    globalThis.fetch = originalFetch;
});

function node(title: string, metadata: CanvasNodeData["metadata"], type: CanvasNodeData["type"] = CanvasNodeType.Image): CanvasNodeData {
    return { id: title, title, metadata, type, position: { x: 0, y: 0 }, width: 320, height: 180 };
}

test("selection shows the media count in both existing right-click menus", () => {
    const noop = () => {};
    const image = node("入库图片", { storageKey: "resource:image-one" });
    const audio = node("声音", { content: "data:audio/wav;base64,YQ==" }, CanvasNodeType.Audio);
    const text = node("分镜", { content: "分镜内容" }, CanvasNodeType.Text);
    const nodes = [image, audio, text];
    const renderMenu = (menu: ContextMenuState, selected: CanvasNodeData[]) => renderToStaticMarkup(createElement(CanvasProjectContextMenu, {
        menu, node: image, nodes, selectedNodeIds: new Set(selected.map(({ id }) => id)), selectedCount: selected.length,
        workspaceMode: "professional", isProjectLinked: true, canUndo: false, canRedo: false, canPaste: false,
        screenToCanvas: (x, y) => ({ x, y }), onClose: noop, onAddNode: noop, onAddFolder: noop,
        onChooseStyle: noop, onOpenDirector: noop, onUpload: noop, onOpenAssets: noop, onOpenProjectCharacters: noop,
        onOpenGenerationHistory: noop, onUndo: noop, onRedo: noop, onPaste: noop, onCopyNode: noop, onCreateGenerationCopy: noop,
        onDuplicate: noop, onDeleteNode: noop, onDeleteConnection: noop, onSaveAsset: noop, onViewMedia: noop,
        onEditText: noop, onOpenDrawing: noop, onGenerateImage: noop, onCopyContent: noop, onCopyMediaUrl: noop,
        onUploadToArkPrivateAsset: noop, onSetAssetCategory: noop, onToggleFrame: noop, onSpreadSelection: noop,
        onCopySelection: noop, onDeleteSelection: noop,
    }));
    const nodeMenu = { type: "node", nodeId: image.id, x: 100, y: 100 } as const;
    const canvasMenu = { type: "canvas", position: { x: 0, y: 0 }, x: 100, y: 100 } as const;
    expect(renderMenu(nodeMenu, nodes)).toContain("批量下载素材 (2)");
    expect(renderMenu(canvasMenu, nodes)).toContain("批量下载素材 (2)");
    expect(renderMenu(nodeMenu, [image])).not.toContain("批量下载素材");
});

test("native batch submits one ordered list of owned media and counts failures", async () => {
    const calls: Array<Array<{ name: string; resourceId: string }>> = [];
    Object.assign(globalThis, { window: { location: { protocol: "wails:" }, go: { main: { DesktopApp: {
        SaveOwnedMedia: () => { throw new Error("unexpected single save dialog"); },
        SaveOwnedMediaBatch: async (entries: Array<{ name: string; resourceId: string }>) => {
            calls.push(entries);
            return { cancelled: false, downloaded: 1, failed: 1 };
        },
    } } } } });
    const nodes = [
        node("入库图片", { storageKey: "resource:image-one", mimeType: "image/webp" }),
        node("外链图片", { content: "https://cdn.example.com/resources/foreign/file" }),
        node("本机视频", { content: "/api/resources/video-two/file", mimeType: "video/quicktime" }, CanvasNodeType.Video),
    ];
    expect(await batchDownloadCanvasMediaNodes(nodes)).toEqual({ cancelled: false, downloaded: 1, failed: 2 });
    expect(calls).toEqual([[
        { name: "01_入库图片.webp", resourceId: "image-one" },
        { name: "03_本机视频.mov", resourceId: "video-two" },
    ]]);
});

test("native batch cancellation is distinct from a failed save", async () => {
    Object.assign(globalThis, { window: { location: { protocol: "wails:" }, go: { main: { DesktopApp: {
        SaveOwnedMediaBatch: async () => ({ cancelled: true, downloaded: 0, failed: 0 }),
    } } } } });
    expect(await batchDownloadCanvasMediaNodes([
        node("本机图片", { storageKey: "resource:owned" }),
        node("外链图片", { content: "https://cdn.example.com/remote.png" }),
    ])).toEqual({ cancelled: true, downloaded: 0, failed: 0 });
});

test("browser batch continues after a failed fetch and starts only verified downloads", async () => {
    const events: string[] = [];
    Object.assign(globalThis, { window: { location: { protocol: "http:" } } });
    globalThis.fetch = (async (input: RequestInfo | URL) => {
        const url = String(input);
        events.push(`start ${url}`);
        await Promise.resolve();
        events.push(`end ${url}`);
        if (url.endsWith("404.png")) return new Response(null, { status: 404 });
        if (url.endsWith("blocked.png")) throw new TypeError("Failed to fetch");
        return new Response("media bytes", { headers: { "Content-Type": "image/png" } });
    }) as typeof fetch;
    expect(await batchDownloadCanvasMediaNodes([
        node("缺失", { content: "https://cdn.example.com/404.png" }),
        node("跨域受限", { content: "https://cdn.example.com/blocked.png" }),
        node("有效", { content: "https://cdn.example.com/ready.png" }),
    ])).toEqual({ cancelled: false, downloaded: 1, failed: 2 });
    expect(events).toEqual([
        "start https://cdn.example.com/404.png", "end https://cdn.example.com/404.png",
        "start https://cdn.example.com/blocked.png", "end https://cdn.example.com/blocked.png",
        "start https://cdn.example.com/ready.png", "end https://cdn.example.com/ready.png",
    ]);
});
