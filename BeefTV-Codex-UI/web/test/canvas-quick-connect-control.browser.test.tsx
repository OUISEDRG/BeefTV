import { afterAll, beforeAll, expect, test } from "bun:test";
import { existsSync } from "node:fs";
import { chromium, type Browser, type Locator, type Page } from "playwright";
import { productCss } from "./fixtures/product-css";

let browser: Browser;
let server: ReturnType<typeof Bun.serve>;

beforeAll(async () => {
    const bundle = await Bun.build({ entrypoints: [import.meta.dir + "/fixtures/canvas-quick-connect-control-harness.tsx"], target: "browser", define: { "import.meta.env.DEV": "false", "import.meta.env.PROD": "true", "import.meta.env.MODE": '"production"', "import.meta.env.VITE_CANVAS_LOCAL_MODE": '"true"', "import.meta.env.VITE_CANVAS_BACKEND_URL": '"/api"', "process.env.NODE_ENV": '"production"' }, plugins: [{ name: "source", setup(builder) {
        builder.onResolve({ filter: /^@\/components\/model-logo$/ }, () => ({ path: "model-logo", namespace: "test-icons" }));
        builder.onLoad({ filter: /.*/, namespace: "test-icons" }, () => ({ contents: "export function ModelLogo() { return null; }", loader: "js" }));
        builder.onResolve({ filter: /^@\// }, args => ({ path: Bun.resolveSync("../src/" + args.path.slice(2), import.meta.dir) }));
    } }] });
    if (!bundle.success) throw new Error(bundle.logs.join("\n"));
    const js = await bundle.outputs.find(output => output.path.endsWith(".js"))!.text();
    const css = await productCss();
    server = Bun.serve({ port: 0, fetch(req) {
        const url = new URL(req.url);
        if (url.pathname === "/harness.js") return new Response(js, { headers: { "Content-Type": "application/javascript" } });
        if (url.pathname === "/harness.css") return new Response(css, { headers: { "Content-Type": "text/css" } });
        if (url.pathname.startsWith("/api/")) return Response.json({ code: 0, data: { items: [], providers: [], total: 0 } });
        return new Response(`<!doctype html><html class="${url.searchParams.get("theme") === "dark" ? "dark" : ""}"><meta name="viewport" content="width=device-width"><link rel="stylesheet" href="/harness.css"><body class="app-product-overlays"><div id="root"></div><script type="module" src="/harness.js"></script></body></html>`, { headers: { "Content-Type": "text/html" } });
    } });
    const executablePath = [process.env.CHROME_PATH, "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"].find((path): path is string => Boolean(path && existsSync(path)));
    browser = await chromium.launch({ executablePath, headless: true });
}, 60000);

afterAll(async () => { await browser?.close(); server?.stop(true); }, 30000);
const state = async (page: Page) => JSON.parse((await page.getByTestId("quick-state").textContent())!);

for (const theme of ["light", "dark"]) {
    test(`快速连线引用区开关透传、键盘操作及零引用状态：${theme}`, async () => {
        const page = await browser.newPage({ viewport: { width: 390, height: 850 } });
        const errors: string[] = [];
        page.on("pageerror", error => errors.push(error.message));
        await page.route("**/*", route => new URL(route.request().url()).origin === server.url.origin ? route.continue() : route.abort());
        try {
            await page.goto(`${server.url}?theme=${theme}`);
            const panel = page.getByTestId("node-panel");
            const toggle = panel.getByRole("button", { name: "快速连线", exact: true });
            await toggle.waitFor();
            expect(await panel.getByRole("group", { name: "已连接素材" }).count()).toBe(0);
            expect(await toggle.getAttribute("aria-pressed")).toBe("false");
            await toggle.focus();
            await page.keyboard.press("Enter");
            expect(await toggle.getAttribute("aria-pressed")).toBe("true");
            const status = panel.getByRole("status", { name: "快速连线状态" });
            expect(await status.textContent()).toContain("按 Esc 或再次点击退出");
            expect(await toggle.getAttribute("aria-describedby")).toBe(await status.getAttribute("id"));
            expect((await state(page)).toggleCount).toBe(1);
            await toggle.click();
            expect(await toggle.getAttribute("aria-pressed")).toBe("false");
            expect(await status.count()).toBe(0);
            expect(await state(page)).toEqual({ active: false, toggleCount: 2, pointerCount: 0, generationCount: 0, prompt: "保持这段提示词和目标节点。", nodeId: "quick-target" });
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
            expect(errors).toEqual([]);
        } finally { await page.close(); }
    }, 30000);
}

test("从放大提示词编辑器开启快速连线时收起弹窗并保持目标", async () => {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    try {
        await page.goto(server.url.toString());
        await page.getByRole("button", { name: "放大编辑提示词" }).click();
        const dialog = page.getByRole("dialog");
        await dialog.getByRole("button", { name: "快速连线", exact: true }).click();
        await dialog.waitFor({ state: "hidden" });
        expect(await page.getByTestId("node-panel").getByRole("button", { name: "快速连线", exact: true }).getAttribute("aria-pressed")).toBe("true");
        expect((await state(page)).toggleCount).toBe(1);
        expect((await state(page)).nodeId).toBe("quick-target");
        expect((await state(page)).generationCount).toBe(0);
    } finally { await page.close(); }
}, 30000);

test("未提供回调的提示词面板保留现有界面", async () => {
    const page = await browser.newPage();
    try {
        await page.goto(`${server.url}?noToggle=1`);
        await page.getByRole("textbox", { name: "图片提示词" }).waitFor();
        expect(await page.getByRole("button", { name: "快速连线", exact: true }).count()).toBe(0);
    } finally { await page.close(); }
}, 30000);

async function selectPromptText(editor: Locator, start: number, end = start) {
    await editor.evaluate((element, rangeOffsets) => {
        element.focus();
        const text = Array.from(element.childNodes).find(node => node.nodeType === Node.TEXT_NODE && node.textContent?.includes("左边右边"));
        if (!text) throw new Error("找不到提示词正文");
        const offset = text.textContent!.indexOf("左边右边");
        const range = document.createRange();
        range.setStart(text, offset + rangeOffsets.start);
        range.setEnd(text, offset + rangeOffsets.end);
        window.getSelection()?.removeAllRanges();
        window.getSelection()?.addRange(range);
    }, { start, end });
}

test("连续点击同名参考图按素材身份插入光标处并继续输入", async () => {
    const page = await browser.newPage();
    try {
        await page.goto(`${server.url}?references=1`);
        const editor = page.getByRole("textbox", { name: "图片提示词" });
        await selectPromptText(editor, 2);
        const shelf = page.getByRole("group", { name: "已连接素材" });
        const thumbnail = (id: string) => shelf.locator(`[data-reference-node-id="${id}"] .canvas-node-reference-preview`);
        expect(await thumbnail("unconnected").count()).toBe(0);
        await thumbnail("reference-b").click({ clickCount: 2 });
        await thumbnail("reference-a").click();
        expect(await editor.evaluate(element => document.activeElement === element)).toBe(true);
        await page.keyboard.insertText("继续");
        expect((await state(page)).prompt).toBe("  第一行。\n左边@[node:reference-b] @[node:reference-b] @[node:reference-a] 继续右边，仍引用 @[node:reference-a]。  ");
        expect((await state(page)).generationCount).toBe(0);
    } finally { await page.close(); }
}, 30000);

test("放大编辑器的缩略图可用键盘替换原选区", async () => {
    const page = await browser.newPage();
    try {
        await page.goto(`${server.url}?references=1`);
        await page.getByRole("button", { name: "放大编辑提示词" }).click();
        const dialog = page.getByRole("dialog");
        const editor = dialog.getByRole("textbox", { name: "图片提示词" });
        await selectPromptText(editor, 0, 2);
        const thumbnail = dialog.locator('[data-reference-node-id="reference-b"] .canvas-node-reference-preview');
        await thumbnail.focus();
        await page.evaluate(() => window.getSelection()?.removeAllRanges());
        await page.keyboard.press("Enter");
        expect((await state(page)).prompt).toBe("  第一行。\n@[node:reference-b] 右边，仍引用 @[node:reference-a]。  ");
        expect(await editor.locator('[data-mention-token="@[node:reference-b]"]').count()).toBe(1);
        expect(await editor.evaluate(element => document.activeElement === element)).toBe(true);
    } finally { await page.close(); }
}, 30000);

test("参考图预览可以独立打开且保留原提示词", async () => {
    const page = await browser.newPage();
    try {
        await page.goto(`${server.url}?references=1`);
        const chip = page.locator('[data-reference-node-id="reference-b"]');
        const insert = chip.getByRole("button", { name: "插入 @图片2", exact: true });
        const preview = chip.getByRole("button", { name: "预览 同名参考图", exact: true });
        await preview.waitFor();
        expect(await insert.count()).toBe(1);
        await preview.focus();
        await page.keyboard.press("Enter");
        await page.locator(".ant-image-preview-img").waitFor();
        expect((await state(page)).prompt).toBe("  第一行。\n左边右边，仍引用 @[node:reference-a]。  ");
    } finally { await page.close(); }
}, 30000);
