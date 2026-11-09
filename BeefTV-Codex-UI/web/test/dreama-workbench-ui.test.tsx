import { expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { DreamaSnapshotPreview, DreamaTaskRow } from "../src/pages/canvas/dreama-workbench";

test("snapshot preview exposes final slots, voices and readable parameters without a confirmation", () => {
    const html = renderToStaticMarkup(<DreamaSnapshotPreview onFocus={() => {}} preview={{
        binding: { schemaVersion: 1, canvasId: "c", nodeId: "n", projectId: 41, seriesId: 12, promptMode: "drama", aspectRatio: "16:9", bindingRevision: 2 },
        model: "model", mode: "video", prompt: "  原文\n@图片2  ", parameters: { size: "16:9", vquality: "3K" },
        media: [{ kind: "图片", index: 2, id: "person", name: "主角" }], voices: [{ image_index: 2, name: "主角原声", sample: true }],
    }} />);
    expect(html).toContain("  原文\n@图片2  ");
    expect(html).toContain("@图片2");
    expect(html).toContain("主角原声");
    expect(html).toContain("分辨率");
    expect(html).toContain("3K");
    expect(html).not.toContain("确认生成");
});

test("ordinary generation and historical countdown records belong to the native task center", () => {
    const task = { id: "job", kind: "video", cancelable: true, autoExecuteAt: Date.now() + 5000, projectId: 41, seriesId: 12, canvasId: "c", nodeId: "n", pointsEstimate: 9 };
    for (const status of ["preparing", "prepared", "countdown", "queued", "processing", "submitted", "completed", "cancelled"]) {
        expect(renderToStaticMarkup(<DreamaTaskRow task={{ ...task, status }} onOpen={() => {}} />)).toBe("");
    }
});

test("workbench displays preflight and recovery exceptions without generation controls", () => {
    const base = { id: "job", kind: "video", cancelable: false, projectId: 41, seriesId: 12, canvasId: "c", nodeId: "n" };
    const exactQuote = "1.000000000000000000000000001";
    for (const [status, stage, label] of [
        ["prepared", "budget_blocked", "预算待配置或不足"],
        ["submission_unknown", "submit", "提交结果待核对"], ["download_failed", "save", "结果待取回"],
        ["binding_verification_required", "terminal", "项目归属待核验"],
    ] as const) {
        const task = { ...base, status, stage, pointsEstimate: exactQuote, cancelable: true, autoExecuteAt: Date.now() + 5000 };
        const html = renderToStaticMarkup(<DreamaTaskRow task={task} onOpen={() => {}} />);
        expect(html).toContain(label);
        expect(html).toContain(exactQuote);
        expect(html).not.toContain("取消");
        expect(html).not.toContain("秒后执行");
        expect(html).not.toContain("立即执行");
        expect(html).not.toContain(`>${stage}<`);
    }
});
