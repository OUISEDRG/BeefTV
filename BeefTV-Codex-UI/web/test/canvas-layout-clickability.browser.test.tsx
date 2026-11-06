import { afterAll, beforeAll, expect, test } from "bun:test";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import postcss from "postcss";
import tailwind from "@tailwindcss/postcss";
import { chromium, type Browser, type Page } from "playwright";

let browser: Browser;
let page: Page;
const errors: string[] = [];

beforeAll(async () => {
    const build = await Bun.build({ entrypoints: [import.meta.dir + "/fixtures/canvas-layout-clickability-harness.tsx"], target: "browser", format: "esm",
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
    page = await browser.newPage({ viewport: { width: 1100, height: 650 } });
    page.setDefaultTimeout(8000);
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/*", route => {
        const url = new URL(route.request().url());
        if (url.origin !== "https://beeftv-layout.test") return route.abort();
        if (url.pathname === "/app.js") return route.fulfill({ contentType: "text/javascript", body: script });
        if (url.pathname === "/style.css") return route.fulfill({ contentType: "text/css", body: css });
        if (url.pathname !== "/") return route.fulfill({ status: 404, body: "fixture only" });
        return route.fulfill({ contentType: "text/html", body: '<html class="dark"><head><link rel="stylesheet" href="/style.css"></head><body style="margin:0;background:#171b24"><div id="root"></div><script type="module" src="/app.js"></script></body></html>' });
    });
}, 60000);

afterAll(async () => { await browser?.close(); });

test("mixed imported images retain clear clickable titles and card bodies at 25% zoom", async () => {
    await page.goto("https://beeftv-layout.test/");
    await page.locator('[data-node-id="asset-7"] .canvas-node-external-header').waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll('[data-node-id] img')].length >= 8 && [...document.querySelectorAll('[data-node-id] img')].every(img => (img as HTMLImageElement).complete));
    const before = JSON.parse(await page.getByTestId("nodes").textContent() || "[]");
    const rectangles = await page.locator("[data-node-id]").evaluateAll(nodes => nodes.map(node => {
        const header = node.querySelector(".canvas-node-external-header")!.getBoundingClientRect();
        const card = node.querySelector(".canvas-node-shell")!.getBoundingClientRect();
        return { id: node.getAttribute("data-node-id"), header: { x: header.x, y: header.y, w: header.width, h: header.height }, card: { x: card.x, y: card.y, w: card.width, h: card.height } };
    }));
    const overlap = (a: { x: number; y: number; w: number; h: number }, b: typeof a) => Math.min(a.x + a.w, b.x + b.w) > Math.max(a.x, b.x) + 0.5 && Math.min(a.y + a.h, b.y + b.h) > Math.max(a.y, b.y) + 0.5;
    expect(rectangles.length).toBe(8);
    for (let i = 0; i < rectangles.length; i++) {
        expect(rectangles[i].header.w).toBeGreaterThanOrEqual(159);
        for (let j = i + 1; j < rectangles.length; j++) {
            for (const first of [rectangles[i].header, rectangles[i].card]) for (const second of [rectangles[j].header, rectangles[j].card]) expect(overlap(first, second)).toBe(false);
        }
    }
    for (const node of before) {
        const card = page.locator(`[data-node-id="${node.id}"] .canvas-node-shell`);
        await card.click();
        expect(await page.getByTestId("clicked").textContent()).toBe(node.id);
        await page.getByRole("button", { name: `编辑节点名称：${node.title}`, exact: true }).click();
        const title = page.getByRole("textbox", { name: "节点名称", exact: true });
        await title.fill(node.title + "✓");
        await title.press("Enter");
        expect(await page.getByTestId("renamed").textContent()).toBe(node.id);
    }
    const after = JSON.parse(await page.getByTestId("nodes").textContent() || "[]");
    expect(after.map(({ position, width, height }: any) => ({ position, width, height }))).toEqual(before.map(({ position, width, height }: any) => ({ position, width, height })));
    expect(errors).toEqual([]);
    if (process.env.BEEFTV_LAYOUT_EVIDENCE) await page.screenshot({ path: process.env.BEEFTV_LAYOUT_EVIDENCE, fullPage: true });
}, 30000);
