import { describe, expect, test } from "bun:test";
import { createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { CanvasNodeContent } from "@/components/canvas/canvas-node-content";
import { hasCanvasMediaSource } from "@/lib/canvas/canvas-media-source";
import { canvasNodeToAsset } from "@/lib/canvas/canvas-node-asset";
import { canvasThemes } from "@/lib/canvas-theme";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";

function image(storageKey?: string): CanvasNodeData {
    return { id: "imported", type: CanvasNodeType.Image, title: "已导入图片", position: { x: 0, y: 0 }, width: 320, height: 180, metadata: { content: "", storageKey, status: "success" } };
}

function markup(node: CanvasNodeData, isBatchRoot = false) {
    return renderToStaticMarkup(<CanvasNodeContent node={node} theme={canvasThemes.dark} isEditingContent={false} textareaRef={createRef<HTMLTextAreaElement>()}
        isBatchRoot={isBatchRoot} batchCount={isBatchRoot ? 2 : 0} batchExpanded={false} batchOpening={false} batchRecovering={false}
        onContentChange={() => {}} onStopEditing={() => {}} mentionReferences={[]} />);
}

describe("durable canvas media sources", () => {
    test("recognizes stored resources and scoped local media without content", () => {
        for (const key of ["resource:saved-image", "image:local:imported", "generation-image:local:task%3A1", "video:local:video", "generation-video:local:task%3A2", "audio:local:clip", "  video:user:durable  ", "legacy-import-key"]) {
            expect(hasCanvasMediaSource(image(key))).toBe(true);
        }
        expect(hasCanvasMediaSource({ metadata: { content: "https://example.test/image.png" } })).toBe(true);
    });

    test("does not mistake incomplete keys, whitespace or previews for an existing media source", () => {
        for (const key of [undefined, "", " ", "resource:", "resource:../invalid"]) {
            expect(hasCanvasMediaSource(image(key))).toBe(false);
        }
        expect(hasCanvasMediaSource({ metadata: { content: " ", previewContent: "preview.png" } })).toBe(false);
    });

    test("old imported images enter the resource loader, including batch roots", () => {
        const node = image("resource:saved-image");
        for (const batch of [false, true]) {
            const output = markup(node, batch);
            expect(output).toContain('aria-label="正在加载图片"');
            expect(output).not.toContain("canvas-node-empty-image-mark");
        }
        expect(node.metadata?.content).toBe("");
        expect(node.metadata?.storageKey).toBe("resource:saved-image");
    });

    test("truly empty image nodes retain their empty state", () => {
        expect(markup(image())).toContain("canvas-node-empty-image-mark");
        expect(markup(image("resource:"))).not.toContain('aria-label="正在加载图片"');
    });

    test("asset conversion shares the source contract and keeps the resource identity", () => {
        const asset = canvasNodeToAsset(image("resource:saved-image"), { canvasId: "canvas", source: "canvas-upload" });
        expect(asset?.kind).toBe("image");
        if (asset?.kind === "image") expect(asset.data.storageKey).toBe("resource:saved-image");
        expect(canvasNodeToAsset(image("resource:"), { canvasId: "canvas", source: "canvas-upload" })).toBeNull();
    });
});
