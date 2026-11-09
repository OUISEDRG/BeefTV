import { describe, expect, test } from "bun:test";

import { buildImageGenerationNodeTitle } from "@/lib/canvas/canvas-generation-title";
import { buildBatchDownloadFilename, buildCanvasMediaDownloadFileName, canvasMediaFileExtension, collectDownloadableMediaNodes, mediaFileExtension, sanitizeDownloadFileName } from "@/lib/canvas/canvas-media-download";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";

function mediaNode(overrides: Partial<CanvasNodeData> = {}): CanvasNodeData {
    return {
        id: "image-1",
        type: CanvasNodeType.Image,
        title: "女明星角色三视图",
        position: { x: 0, y: 0 },
        width: 1024,
        height: 1024,
        metadata: { content: "data:image/png;base64,aW1hZ2U=", mimeType: "image/png", status: "success" },
        ...overrides,
    };
}

describe("canvas media download", () => {
    test("框选下载过滤非媒体和空节点，并按从上到下、每行从左到右排列", () => {
        const image = mediaNode({ id: "image", position: { x: 500, y: 100 } });
        const video = mediaNode({ id: "video", type: CanvasNodeType.Video, position: { x: 200, y: 140 }, metadata: { storageKey: "resource:owned-video" } });
        const audio = mediaNode({ id: "audio", type: CanvasNodeType.Audio, position: { x: 10, y: 180 }, metadata: { content: "https://example.com/voice.wav" } });
        const text = mediaNode({ id: "text", type: CanvasNodeType.Text });
        const empty = mediaNode({ id: "empty", metadata: { content: "   " } });
        const unselected = mediaNode({ id: "unselected" });
        const selection = new Set(["audio", "image", "text", "empty", "video"]);
        for (const nodes of [[audio, text, image, unselected, empty, video], [image, video, empty, audio, unselected, text]]) {
            expect(collectDownloadableMediaNodes(nodes, selection).map((node) => node.id)).toEqual(["video", "image", "audio"]);
        }
    });

    test("批量下载文件名按批次宽度补零并使用素材真实格式", () => {
        const video = mediaNode({ type: CanvasNodeType.Video, title: "镜头1:开场/雨夜?", metadata: { content: "https://example.com/shot.mov", mimeType: "video/quicktime" } });
        const audio = mediaNode({ type: CanvasNodeType.Audio, title: "角色对白.wav", metadata: { storageKey: "resource:owned-audio", mimeType: "audio/x-wav" } });
        expect(buildBatchDownloadFilename(video, 0, 8)).toBe("01_镜头1_开场_雨夜.mov");
        expect(buildBatchDownloadFilename(audio, 2, 120)).toBe("003_角色对白.wav");
        expect(buildBatchDownloadFilename(mediaNode({ title: "  " }), 1, 8)).toBe("02_图片.png");
    });

    test("素材库 MIME 别名经过文件名清理后仍保留真实格式后缀", () => {
        for (const [mime, extension] of [["video/quicktime", "mov"], ["image/svg+xml", "svg"], ["audio/x-wav", "wav"], ["audio/mpeg", "mp3"], ["audio/mp4", "m4a"], ["image/jpeg; charset=utf-8", "jpg"]]) {
            const name = sanitizeDownloadFileName(`中文素材.${mediaFileExtension(mime)}`);
            expect(name).toBe(`中文素材.${extension}`);
        }
        expect(mediaFileExtension("application/octet-stream", "https://example.com/clip.webm?token=hidden")).toBe("webm");
        expect(mediaFileExtension("application/octet-stream", "blob:unknown")).toBeUndefined();
    });

    test("按画布名、节点名和本地日期生成文件名", () => {
        expect(buildCanvasMediaDownloadFileName("写给阿妈的情书", mediaNode(), new Date(2026, 7, 28, 12))).toBe("写给阿妈的情书_女明星角色三视图_20260828.png");
    });

    test("清理跨平台非法字符且保留中文可读名称", () => {
        const node = mediaNode({ title: "女明星/角色:三视图?" });
        expect(buildCanvasMediaDownloadFileName("写给阿妈的情书|终稿", node, new Date(2026, 7, 28, 12))).toBe("写给阿妈的情书_终稿_女明星_角色_三视图_20260828.png");
    });

    test("截断长名称后不会留下 Windows 不接受的结尾句点", () => {
        const node = mediaNode({ title: `${"a".repeat(79)}.extra` });
        expect(buildCanvasMediaDownloadFileName("画布", node, new Date(2026, 7, 28, 12))).toBe(`画布_${"a".repeat(79)}_20260828.png`);
    });

    test("优先按媒体 MIME 类型确定视频扩展名", () => {
        expect(canvasMediaFileExtension(mediaNode({ type: CanvasNodeType.Video, metadata: { content: "https://example.com/video", mimeType: "video/webm" } }))).toBe("webm");
        expect(canvasMediaFileExtension(mediaNode({ type: CanvasNodeType.Video, metadata: { content: "https://example.com/video", mimeType: "video/quicktime" } }))).toBe("mov");
    });

    test("MIME 类型不明确时从远程资源 URL 识别扩展名", () => {
        expect(canvasMediaFileExtension(mediaNode({ metadata: { content: "https://example.com/result.jpeg?token=hidden", mimeType: "image/*" } }))).toBe("jpg");
    });

    test("导出文件名去掉路径和非法字符", () => {
        expect(sanitizeDownloadFileName("../角色:三视图?.png")).toBe("角色_三视图.png");
    });

    test("audio/wave 与其它 WAV 别名下载为 .wav，不回落到 mp3", () => {
        const now = new Date(2026, 8, 24, 12);
        for (const mimeType of ["audio/wave", "audio/wav", "audio/x-wav", "audio/vnd.wave"]) {
            const node = mediaNode({
                type: CanvasNodeType.Audio,
                title: "旁白",
                metadata: { content: "blob:http://localhost/tts", mimeType, status: "success" },
            });
            expect(canvasMediaFileExtension(node)).toBe("wav");
            expect(buildCanvasMediaDownloadFileName("画布", node, now)).toBe("画布_旁白_20260924.wav");
        }
    });
});

describe("generated image title", () => {
    test("普通节点继续使用提示词摘要", () => {
        expect(buildImageGenerationNodeTitle("一座云层中的未来城市", mediaNode({ title: "原图" }))).toBe("一座云层中的未来城市");
    });

    test("快捷键复制节点生成后保留 copy 序号", () => {
        const source = mediaNode({ title: "未来城市_copy2", metadata: { copiedFromNodeId: "source" } });
        expect(buildImageGenerationNodeTitle("未来城市", source)).toBe("未来城市_copy2");
    });

    test("参数变体生成后保留版本字母", () => {
        const source = mediaNode({ title: "未来城市 · C", metadata: { versionOfNodeId: "source", versionLabel: "C" } });
        expect(buildImageGenerationNodeTitle("未来城市", source)).toBe("未来城市 · C");
    });

    test("复制变体和批量输出使用互不重复的标题", () => {
        const source = mediaNode({ title: "未来城市_copy1 · B", metadata: { copiedFromNodeId: "source", versionOfNodeId: "source", versionLabel: "B" } });
        expect([0, 1].map((index) => buildImageGenerationNodeTitle("未来城市", source, index, 2))).toEqual(["未来城市_copy1 · B · 1", "未来城市_copy1 · B · 2"]);
    });

    test("下载文件名继承 copy 和版本标识，不再互相重复", () => {
        const now = new Date(2026, 7, 29, 12);
        const copy = mediaNode({ title: buildImageGenerationNodeTitle("未来城市", mediaNode({ title: "未来城市_copy1", metadata: { copiedFromNodeId: "source" } })) });
        const variant = mediaNode({ title: buildImageGenerationNodeTitle("未来城市", mediaNode({ title: "未来城市 · C", metadata: { versionOfNodeId: "source", versionLabel: "C" } })) });
        expect(buildCanvasMediaDownloadFileName("自由画布", copy, now)).toBe("自由画布_未来城市_copy1_20260829.png");
        expect(buildCanvasMediaDownloadFileName("自由画布", variant, now)).toBe("自由画布_未来城市 · C_20260829.png");
    });
});
