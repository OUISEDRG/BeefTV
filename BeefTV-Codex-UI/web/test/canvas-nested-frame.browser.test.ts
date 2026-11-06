import { afterAll, beforeAll, expect, test } from "bun:test";
import { existsSync } from "node:fs";
import { chromium, type Browser, type Page } from "playwright";

let browser: Browser;
let page: Page;
beforeAll(async () => {
    const build = await Bun.build({ entrypoints: [import.meta.dir + "/fixtures/canvas-nested-frame-harness.tsx"], target: "browser", format: "esm",
        define: { "import.meta.env": "{}", "process.env.NODE_ENV": '"production"' }, plugins: [{ name: "alias", setup(builder) {
            builder.onResolve({ filter: /^@\// }, args => ({ path: Bun.resolveSync("../src/" + args.path.slice(2), import.meta.dir) }));
        } }] });
    if (!build.success) throw new Error(build.logs.join("\n"));
    const script = await build.outputs.find(output => output.path.endsWith(".js"))!.text();
    const executablePath = [process.env.CHROME_PATH, "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].find((path): path is string => Boolean(path && existsSync(path)));
    browser = await chromium.launch({ headless: true, executablePath });
    page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
    page.setDefaultTimeout(5000);
    await page.route("https://beeftv-nested.test/**", route => route.fulfill(new URL(route.request().url()).pathname === "/app.js"
        ? { contentType: "text/javascript", body: script }
        : { contentType: "text/html", body: '<div id="root"></div><script type="module" src="/app.js"></script>' }));
}, 30000);
afterAll(async () => { await browser?.close(); });

for (const multi of [false, true]) test(`dragging the episode moves all nested contents once (outer+grandchild selected=${multi})`, async () => {
    await page.goto("https://beeftv-nested.test/" + (multi ? "?multi=1" : ""));
    await page.getByTestId("outer").waitFor();
    const before = JSON.parse(await page.getByTestId("positions").textContent() || "[]");
    await page.mouse.move(40, 120); await page.mouse.down(); await page.mouse.move(120, 180, { steps: 5 });
    await page.waitForFunction(() => JSON.parse(document.querySelector('[data-testid="preview"]')!.textContent!).length === 4);
    expect(new Set(JSON.parse(await page.getByTestId("preview").textContent() || "[]"))).toEqual(new Set(["outer", "inner", "video", "image"]));
    await page.mouse.up();
    await page.waitForFunction(() => JSON.parse(document.querySelector('[data-testid="positions"]')!.textContent!)[0].position.x !== 20);
    const after = JSON.parse(await page.getByTestId("positions").textContent() || "[]");
    for (let index = 0; index < before.length; index++) {
        const delta = before[index].id === "outside" ? { x: 0, y: 0 } : { x: 80, y: 60 };
        expect(after[index].position).toEqual({ x: before[index].position.x + delta.x, y: before[index].position.y + delta.y });
        expect(after[index].parentId).toBe(before[index].parentId);
    }
}, 15000);

test("dragging a storyboard keeps its episode ownership and carries the media", async () => {
    await page.goto("https://beeftv-nested.test/");
    await page.getByTestId("inner").waitFor();
    await page.mouse.move(100, 195); await page.mouse.down(); await page.mouse.move(160, 235, { steps: 5 }); await page.mouse.up();
    await page.waitForFunction(() => JSON.parse(document.querySelector('[data-testid="positions"]')!.textContent!)[1].position.x !== 80);
    const after = JSON.parse(await page.getByTestId("positions").textContent() || "[]");
    expect(after[0].position).toEqual({ x: 20, y: 100 });
    expect(after[1]).toMatchObject({ parentId: "outer", position: { x: 140, y: 220 } });
    expect(after[2]).toMatchObject({ parentId: "inner", position: { x: 190, y: 280 } });
    expect(after[3]).toMatchObject({ parentId: "inner", position: { x: 340, y: 280 } });
}, 15000);
