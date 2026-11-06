import { afterAll, beforeAll, expect, test } from "bun:test";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import postcss from "postcss";
import tailwind from "@tailwindcss/postcss";
import { chromium, type Browser, type Page } from "playwright";

let browser: Browser;
let page: Page;
const errors: string[] = [];
let posts = 0;
beforeAll(async () => {
    const build = await Bun.build({ entrypoints: [import.meta.dir + "/fixtures/canvas-quick-connect-integration-harness.tsx"], target: "browser", format: "esm",
        define: { "import.meta.env": "{}", "process.env.NODE_ENV": '"production"' }, plugins: [{ name: "alias", setup(builder) {
            builder.onResolve({ filter: /^@\/components\/model-logo$/ }, () => ({ path: "model-logo", namespace: "fixture-icons" }));
            builder.onLoad({ filter: /.*/, namespace: "fixture-icons" }, () => ({ contents: "export function ModelLogo() { return null; }", loader: "js" }));
            builder.onLoad({ filter: /\.(css|svg|png|jpe?g|gif|webp|woff2?)$/ }, () => ({ contents: "export default ''", loader: "js" }));
            builder.onResolve({ filter: /^@\// }, args => ({ path: Bun.resolveSync("../src/" + args.path.slice(2), import.meta.dir) }));
        } }] });
    if (!build.success) throw new Error(build.logs.join("\n"));
    const script = await build.outputs.find(output => output.path.endsWith(".js"))!.text();
    const cssPath = resolve(import.meta.dir, "../src/styles/globals.css");
    const css = (await postcss([tailwind({ base: resolve(import.meta.dir, "..") })]).process(await Bun.file(cssPath).text(), { from: cssPath })).css;
    const executablePath = [process.env.CHROME_PATH, "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].find((path): path is string => Boolean(path && existsSync(path)));
    browser = await chromium.launch({ headless: true, executablePath });
    page = await browser.newPage({ viewport: { width: 1100, height: 800 } });
    page.setDefaultTimeout(5000);
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/*", route => {
        if (route.request().method() !== "GET") posts++;
        const url = new URL(route.request().url());
        if (url.origin !== "https://beeftv-quick.test") return route.abort();
        if (url.pathname === "/app.js") return route.fulfill({ contentType: "text/javascript", body: script });
        if (url.pathname === "/style.css") return route.fulfill({ contentType: "text/css", body: css });
        if (url.pathname !== "/") return route.fulfill({ status: 404, body: "fixture only" });
        return route.fulfill({ contentType: "text/html", body: '<html class="dark"><head><link rel="stylesheet" href="/style.css"></head><body style="margin:0;background:#171b24;color:white"><div id="root"></div><script type="module" src="/app.js"></script></body></html>' });
    });
}, 60000);
afterAll(async () => { await browser?.close(); });

const state = async () => JSON.parse(await page.getByTestId("state").textContent() || "{}");
const body = (id: string) => page.locator(`[data-node-id="${id}"] .canvas-node-shell`);
async function mount(query = "") {
    errors.length = 0;
    await page.goto("https://beeftv-quick.test/" + query);
    await page.getByRole("button", { name: "快速连线", exact: true }).click();
    await page.waitForFunction(() => JSON.parse(document.querySelector('[data-testid="state"]')!.textContent!).active === "target");
}

test("single and repeated quick sources keep the target panel and actual reference order", async () => {
    await mount();
    await body("image-one").click();
    await body("image-two").click();
    await body("image-one").click();
    const current = await state();
    expect(current.references).toEqual(["image-one", "image-two"]);
    expect(current.connections).toHaveLength(3);
    expect(current.active).toBe("target");
    expect(current.dialogNodeId).toBe("target");
    expect(current.selected).toBe("target");
    expect(current.cancelCount).toBe(1);
    expect(await page.getByTestId("feedback").innerText()).toContain("无需重复");
    expect(await page.getByTestId("panel").innerText()).toContain("Keep target prompt · 引用 2");
});

test("a selected Dreama model accepts ordinary audio and then image references while keeping the target fixed", async () => {
    await mount("?audioSupported=1");
    await body("audio").click();
    expect((await state()).references).toEqual(["audio"]);
    await body("image-one").click();
    await body("audio").click();
    const current = await state();
    expect(current.references).toEqual(["audio", "image-one"]);
    expect(current.connections.filter((edge: any) => edge.fromNodeId === "audio" && edge.toNodeId === "target")).toHaveLength(1);
    expect(current.active).toBe("target");
    expect(current.dialogNodeId).toBe("target");
    expect(current.selected).toBe("target");
    expect(await page.getByTestId("panel").innerText()).toContain("引用 2");
    expect(posts).toBe(0);
    expect(errors).toEqual([]);
});

test("an unsupported audio-only mode shows the selected mode restriction and accepts audio after adding an image", async () => {
    await mount("?audioDisabledMode=1");
    await body("audio").click();
    expect((await state()).references).toEqual([]);
    expect(await page.locator("[data-canvas-connection-feedback]").isVisible()).toBe(true);
    expect(await page.getByTestId("feedback").innerText()).toContain("当前模型在此模式下的接入配置未开放参考音频");
    expect((await state()).dialogNodeId).toBe("target");
    await body("image-one").click();
    await body("audio").click();
    expect((await state()).references).toEqual(["image-one", "audio"]);
    expect((await state()).active).toBe("target");
    expect(errors).toEqual([]);
});

test("three ordinary videos connect to the fixed target once each without becoming single-source extension", async () => {
    await mount("?videosSupported=1");
    await body("video").click();
    await body("video-two").click();
    await body("video-three").click();
    await body("video-two").click();
    const current = await state();
    expect(current.references).toEqual(["video", "video-two", "video-three"]);
    expect(current.connections.filter((edge: any) => edge.toNodeId === "target")).toHaveLength(3);
    expect(current.dialogNodeId).toBe("target");
    expect(current.selected).toBe("target");
    expect(current.active).toBe("target");
    expect(current.plays).toBe(0);
    expect(posts).toBe(0);
    expect(errors).toEqual([]);
});

test("clicking a collapsed nested group expands real members once without toggling the group", async () => {
    await mount();
    await page.locator('[data-node-id="group"] .canvas-frame-shell').click();
    expect(await page.getByTestId("feedback").innerText()).toContain("跳过 2");
    expect(await page.locator("[data-canvas-connection-feedback]").isVisible()).toBe(true);
    await page.locator('[data-node-id="group"] .canvas-frame-shell').click();
    const current = await state();
    expect(current.references).toEqual(["group-one", "group-two"]);
    expect(current.connections.filter((edge: any) => edge.fromNodeId === "group" || edge.fromNodeId === "inner")).toEqual([]);
    expect(current.collapses).toBe(0);
    expect(current.selected).toBe("target");
    expect(current.dialogNodeId).toBe("target");
});

test("empty images and loading videos are rejected as quick sources consistently with group members", async () => {
    await mount();
    await body("empty-image").click();
    expect(await page.getByTestId("feedback").innerText()).toContain("没有可引用的媒体素材");
    expect(await page.locator("[data-canvas-connection-feedback]").isVisible()).toBe(true);
    await body("loading-video").click();
    expect(await page.getByTestId("feedback").innerText()).toContain("尚未就绪");
    expect((await state()).references).toEqual([]);
    expect((await state()).selected).toBe("target");
});

test("invalid, self, cycle and incompatible sources show visible reasons without new references", async () => {
    await mount();
    await page.locator('[data-node-id="empty-group"] .canvas-frame-shell').click();
    expect(await page.getByTestId("feedback").innerText()).toContain("没有可引用");
    await body("target").click();
    expect(await page.getByTestId("feedback").innerText()).toContain("自身");
    await body("ancestor").click();
    expect(await page.getByTestId("feedback").innerText()).toContain("循环");
    expect((await state()).references).toEqual([]);
    await mount("?imageTarget=1");
    await body("video").click();
    expect(await page.getByTestId("feedback").innerText()).toContain("参考视频");
    expect((await state()).references).toEqual([]);
});

test("quick clicks suppress rename, video play and image double-click preview until Escape", async () => {
    await mount();
    await page.getByRole("button", { name: "编辑节点名称：image-one", exact: true }).click();
    expect(await page.getByRole("textbox", { name: "节点名称", exact: true }).count()).toBe(0);
    await page.getByRole("button", { name: "播放 video", exact: true }).click();
    await body("image-two").dblclick();
    const quick = await state();
    expect(quick.plays).toBe(0);
    expect(quick.previews).toBe(0);
    expect(quick.renames).toBe(0);
    expect(quick.dialogNodeId).toBe("target");
    await page.keyboard.press("Escape");
    expect((await state()).active).toBeNull();
    await page.getByRole("button", { name: "播放 video", exact: true }).click();
    expect((await state()).plays).toBe(1);
    await page.getByRole("button", { name: "编辑节点名称：image-one", exact: true }).click();
    expect(await page.getByRole("textbox", { name: "节点名称", exact: true }).count()).toBe(1);
    await page.keyboard.press("Escape");
    await body("image-two").click();
    expect((await state()).selected).toBe("image-two");
    expect((await state()).dialogNodeId).toBe("image-two");
});

for (const action of ["toggle", "close panel", "switch project", "delete target", "read only", "other panel"]) test(`quick session exits when ${action}`, async () => {
    await mount();
    await page.getByRole("button", { name: action === "toggle" ? "快速连线" : action, exact: true }).click();
    expect((await state()).active).toBeNull();
    const before = (await state()).connections.length;
    await body("image-one").click();
    expect((await state()).connections.length).toBe(before);
    expect((await state()).selected).toBe("image-one");
});

test("overview fallback obeys primary-button capture and becomes ordinary interaction after stopping", async () => {
    await mount();
    await page.getByTestId("lod-source").click({ button: "right" });
    expect((await state()).lod).toEqual({ handled: false, defaultPrevented: false, normal: 1 });
    expect((await state()).references).toEqual([]);
    await page.getByTestId("lod-source").click();
    expect((await state()).lod).toEqual({ handled: true, defaultPrevented: true, normal: 1 });
    expect((await state()).references).toEqual(["image-two"]);
    await page.keyboard.press("Escape");
    await page.getByTestId("lod-source").click();
    expect((await state()).lod).toEqual({ handled: false, defaultPrevented: false, normal: 2 });
});

test("Space plus dragging a source pans the real canvas without adding references and quick clicks resume afterward", async () => {
    await mount();
    // Space over the canvas, not a focused button, starts the real pan gesture.
    await page.evaluate(() => (document.activeElement as HTMLElement)?.blur());
    const source = await body("image-one").boundingBox();
    if (!source) throw new Error("Missing source card");
    const x = source.x + source.width / 2, y = source.y + source.height / 2;
    await page.keyboard.down("Space");
    await page.waitForSelector('[data-canvas-space-pressed="true"]');
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + 90, y + 60, { steps: 6 });
    await page.mouse.up();
    expect((await state()).references).toEqual([]);
    await page.getByTestId("lod-source").click();
    expect((await state()).lod).toEqual({ handled: false, defaultPrevented: false, normal: 1 });
    await page.keyboard.up("Space");
    await page.waitForFunction(() => JSON.parse(document.querySelector('[data-testid="state"]')!.textContent!).viewport.x === 90);
    const panned = await state();
    expect(panned.viewport).toEqual({ x: 90, y: 60, k: 1 });
    expect(panned.references).toEqual([]);
    expect(panned.connections).toHaveLength(1);
    expect(panned.active).toBe("target");
    expect(panned.dialogNodeId).toBe("target");
    expect(panned.selected).toBe("target");
    await body("image-one").click();
    expect((await state()).references).toEqual(["image-one"]);
});

test("the earlier canvas keyboard listener exits quick mode on first Escape and closes the target only on the next Escape", async () => {
    await mount();
    await body("image-one").click();
    await page.keyboard.press("Escape");
    expect((await state()).active).toBeNull();
    expect((await state()).dialogNodeId).toBe("target");
    expect((await state()).selected).toBe("target");
    await page.keyboard.press("Escape");
    expect((await state()).dialogNodeId).toBeNull();
    expect((await state()).selected).toBe("");
});

test("keyboard clicks and rapid repeated source clicks add exactly one edge with no external writes", async () => {
    await mount();
    await body("image-one").evaluate(element => { (element as HTMLElement).click(); (element as HTMLElement).click(); });
    expect((await state()).references).toEqual(["image-one"]);
    expect((await state()).connections).toHaveLength(2);
    expect(posts).toBe(0);
    expect(errors).toEqual([]);
});
