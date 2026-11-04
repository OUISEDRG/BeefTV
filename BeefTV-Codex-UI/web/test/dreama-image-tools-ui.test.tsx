import { expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server.node";

import { buildImageToolbarTools, resolveImageToolbarLayout } from "../src/components/canvas/canvas-image-toolbar-tools";
import { NodeDockToolButton } from "../src/components/canvas/canvas-node-dock-tools";
import { CanvasImageMaskEditor } from "../src/components/canvas/canvas-node-mask-edit-dialog";
import { createModelChannel, defaultConfig, encodeChannelModel } from "../src/stores/use-config-store";
import { defaultModelCapabilityConfig } from "../src/lib/model-capabilities";
import { bindDreamaProject } from "../src/lib/canvas/dreama-workbench";
import { CanvasNodeType, type CanvasNodeData } from "../src/types/canvas";

function fixture() {
    const image = defaultModelCapabilityConfig("openai-image").image!;
    image.size = { parameter: "aspect_ratio", values: ["1:1"], default: "1:1", allowCustom: false };
    image.quality = { supported: true, values: ["3K"], default: "3K" };
    image.references = { ...image.references, maxImages: 3, maskSupported: false };
    const channel = createModelChannel({ id: "dreama-image", enabled: true, baseUrl: "http://127.0.0.1:18765", apiKey: "fixture-only", interfaceType: "dreama-images-async", models: ["image"], modelProfiles: [{ model: "image", capability: "image", protocol: "dreama-images-async", imageCapabilitiesVersion: "v1", capabilityConfig: { version: 1, image } }] });
    const model = encodeChannelModel(channel.id, "image");
    const config = { ...defaultConfig, channels: [channel], model, imageModel: model, quality: "3K", size: "1:1", count: "1" };
    const node: CanvasNodeData = { id: "source", type: CanvasNodeType.Image, title: "人物", position: { x: 0, y: 0 }, width: 100, height: 100, metadata: { content: "data:image/png;base64,AA==", model, quality: "3K", size: "1:1" } };
    const canvas = { id: "canvas", nodes: [node], integrations: { dreama: bindDreamaProject(undefined, { id: 41, name: "项目", promptMode: "drama", aspectRatio: "1:1" }, 12) } };
    return { config, node, canvas, channel };
}

test("wide and compact image menus hide unsupported edits while exposing texture and local tools", () => {
    const { node, config, canvas } = fixture();
    const tools = buildImageToolbarTools(node, {} as never, config, canvas);
    for (const compact of [false, true]) {
        const layout = resolveImageToolbarLayout(tools, compact);
        const visible = Object.values(layout).flat().map((tool) => tool.id);
        expect(visible).not.toContain("maskEdit");
        expect(visible).not.toContain("emotion");
        for (const id of ["portraitTexture", "angle", "lighting", "crop", "split", "annotation"]) expect(visible).toContain(id);
    }
    expect(tools.find((tool) => tool.id === "portraitTexture")?.description).toBe("基于原图重新生成");
});

test("recoverable missing binding remains visible with an accessible native disabled button and guarded handler", () => {
    const { node, config, canvas } = fixture();
    const calls: string[] = [];
    const tools = buildImageToolbarTools(node, { onAngle: () => calls.push("angle") } as never, config, { ...canvas, integrations: undefined });
    const angle = tools.find((tool) => tool.id === "angle")!;
    expect(angle.disabled).toBe(true);
    expect(angle.disabledReason).toContain("绑定");
    const html = renderToStaticMarkup(<NodeDockToolButton tool={angle} showTooltip={false} />);
    expect(html).toContain('disabled=""');
    expect(html).toContain('aria-description="');
    expect(html).toContain(angle.disabledReason!);
    angle.onClick();
    expect(calls).toEqual([]);
});

test("non-Dreama menu still exposes mask, expression, texture and dispatches the source node", () => {
    const { node, config, channel } = fixture();
    channel.interfaceType = "openai-image";
    channel.modelProfiles![0]!.protocol = "openai-image";
    const calls: CanvasNodeData[] = [];
    const tools = buildImageToolbarTools(node, { onMaskEdit: (source: CanvasNodeData) => calls.push(source) } as never, config);
    expect(resolveImageToolbarLayout(tools, false).portrait.map((tool) => tool.id)).toEqual(["emotion", "portraitTexture"]);
    const mask = tools.find((tool) => tool.id === "maskEdit")!;
    expect(mask.disabled).toBeUndefined();
    mask.onClick();
    expect(calls).toEqual([node]);
});

test("already-open mask editor renders the unsupported reason before its disabled submit", () => {
    const { node, config } = fixture();
    const html = renderToStaticMarkup(<CanvasImageMaskEditor node={node} imageDimensions={{ width: 100, height: 100 }} scale={1} config={config} onCancel={() => {}} onConfirm={() => { throw new Error("unexpected submit"); }} />);
    expect(html).toContain('role="status"');
    expect(html).toContain("剧梦");
    expect(html).toContain('aria-label="AI 修改"');
    expect(html).toContain('aria-describedby="');
    expect(html).toContain('disabled=""');
});

test("live mask model switching and disabled menu items cannot dispatch", async () => {
    const child = Bun.spawn([process.execPath, "run", "test/fixtures/dreama-image-tool-ui.fixture.tsx"], { cwd: import.meta.dir + "/..", stdout: "pipe", stderr: "pipe" });
    const [status, stdout, stderr] = await Promise.all([child.exited, new Response(child.stdout).text(), new Response(child.stderr).text()]);
    expect({ status, stderr }).toEqual({ status: 0, stderr: "" });
    expect(stdout).toContain("PASS: model changes disable an existing mask editor before submit");
    expect(stdout).toContain("PASS: unavailable menu items retain their explanation and ignore invocation");
    expect(stdout).toContain("PASS: stale emotion metadata disables a tool draft before generation");
    expect(stdout).toContain("PASS: video reverse entry disappears for the Codex bridge in wide and compact toolbars");
}, 15000);
