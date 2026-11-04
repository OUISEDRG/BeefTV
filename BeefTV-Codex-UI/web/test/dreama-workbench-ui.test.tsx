import { expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { DreamaSnapshotPreview, DreamaTaskRow } from "../src/pages/canvas/dreama-workbench";
import { dreamaTaskPresentation } from "../src/lib/canvas/dreama-workbench";

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

test("task cancel control follows the bridge cancelable fact even after submission", () => {
    const task = { id: "job", kind: "video", status: "submitted", cancelable: false, projectId: 41, seriesId: 12, canvasId: "c", nodeId: "n", pointsEstimate: 9 };
    expect(renderToStaticMarkup(<DreamaTaskRow task={task} busy={false} onOpen={() => {}} onCancel={() => {}} />)).not.toContain("取消");
    const pending = renderToStaticMarkup(<DreamaTaskRow task={{ ...task, status: "preparing", cancelable: true }} busy={false} onOpen={() => {}} onCancel={() => {}} />);
    expect(pending).toContain("取消");
    expect(pending).toContain("准备中");
    expect(pending).toContain("预计 9 积分");
});

test("public bridge task status controls terminal labels and all pending or recovery counts", () => {
    const base = { id: "job", kind: "video", cancelable: false, projectId: 41, seriesId: 12, canvasId: "c", nodeId: "n" };
    for (const [status, stage, label, pending] of [
        ["completed", "complete", "已完成", false], ["failed", "terminal", "失败", false], ["cancelled", "cancelled_before_submit", "已取消", false],
        ["prepared", "complete", "等待执行", true], ["queued", "poll", "排队中", true], ["processing", "poll", "生成中", true],
        ["submitting", "submit", "提交中", true], ["preparing", "reference:1", "准备中", true],
        ["submission_unknown", "submit", "提交结果待核对", true], ["download_failed", "save", "结果待取回", true],
        ["binding_verification_required", "terminal", "项目归属待核验", true],
    ] as const) {
        const task = { ...base, status, stage };
        expect(dreamaTaskPresentation(task)).toMatchObject({ label, pending });
        const html = renderToStaticMarkup(<DreamaTaskRow task={task} busy={false} onOpen={() => {}} onCancel={() => {}} />);
        expect(html).toContain(label);
        expect(html).not.toContain(`>${stage}<`);
    }
    expect(dreamaTaskPresentation({ ...base, status: "preparing", stage: "reference:1" }).detail).toBe("准备第 1 项参考素材");
});
