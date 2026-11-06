import { afterAll, beforeAll, expect, test } from "bun:test";
import { existsSync } from "node:fs";
import { chromium, type Browser, type Page } from "playwright";

let browser: Browser;
let page: Page;
beforeAll(async () => {
    const build = await Bun.build({
        entrypoints: [import.meta.dir + "/fixtures/canvas-connection-target-harness.tsx"], target: "browser", format: "esm",
        define: { "import.meta.env": "{}", "process.env.NODE_ENV": '"production"' },
        plugins: [{ name: "alias", setup(builder) {
            builder.onLoad({ filter: /src[\\/]components[\\/]model-logo\.tsx$/ }, () => ({ contents: "export function ModelLogo() { return null; }", loader: "js" }));
            builder.onResolve({ filter: /^@\// }, args => ({ path: Bun.resolveSync("../src/" + args.path.slice(2), import.meta.dir) }));
        } }],
    });
    if (!build.success) throw new Error(build.logs.join("\n"));
    const script = await build.outputs[0].text();
    const executablePath = [process.env.CHROME_PATH, "C:/Program Files/Google/Chrome/Application/chrome.exe", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"].find((path): path is string => Boolean(path && existsSync(path)));
    browser = await chromium.launch({ headless: true, executablePath });
    page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
    await page.route("https://beeftv-connection.test/**", route => route.fulfill(new URL(route.request().url()).pathname === "/app.js"
        ? { contentType: "text/javascript", body: script }
        : { contentType: "text/html", body: '<style>body{margin:0}</style><div id="root"></div><script type="module">import { mountConnectionTargetHarness } from "/app.js"; mountConnectionTargetHarness(document.getElementById("root"));</script>' }));
}, 30_000);
afterAll(async () => { await browser?.close(); });

async function start(scale: number, invalid = false, batch = false) {
    await page.goto(`https://beeftv-connection.test/?scale=${scale}${invalid ? "&invalid" : ""}${batch ? "&batch" : ""}`);
    const pin = page.locator("#source-pin");
    await pin.waitFor();
    const rect = await pin.boundingBox();
    await page.mouse.move(rect!.x + rect!.width / 2, rect!.y + rect!.height / 2);
    await page.mouse.down();
}
async function state() { return JSON.parse(await page.locator("#state").innerText()); }
async function move(x: number, y: number) {
    await page.mouse.move(x, y, { steps: 3 });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

for (const scale of [0.42, 1]) for (const [name, x, y] of [["center", 760, 280], ["corner", 1118, 82], ["halo", 1135, 200]] as const) {
    test(`${scale * 100}% ${name}: highlight and pointer release choose the same existing card`, async () => {
        await start(scale);
        await move(x * scale, y * scale);
        expect((await state()).target).toBe("target");
        await page.mouse.up();
        expect(await state()).toMatchObject({ pending: false, connections: [{ fromNodeId: "source", toNodeId: "target" }] });
    });
}

test("invalid card explains rejection and cannot open the create menu", async () => {
    await start(0.42, true);
    await move(760 * 0.42, 280 * 0.42);
    expect((await state()).target).toBeNull();
    await page.mouse.up();
    expect(await state()).toMatchObject({ pending: false, connections: [] });
    await page.getByText("图片生成节点不能连接参考视频", { exact: true }).waitFor();
});

test("release on an overlay neither connects through it nor creates a node", async () => {
    await start(1);
    await move(740, 370);
    await page.mouse.up();
    expect(await state()).toMatchObject({ pending: false, connections: [] });
});

test("only empty canvas opens the connected-node creation menu", async () => {
    await start(1);
    await move(1400, 650);
    await page.mouse.up();
    expect(await state()).toMatchObject({ pending: true, connections: [] });
});

test("batch dragging uses the same full-card target and releases exactly once", async () => {
    await start(0.42, false, true);
    await move(760 * 0.42, 280 * 0.42);
    expect((await state()).target).toBe("target");
    await page.mouse.up();
    const result = await state();
    expect(result.connections).toHaveLength(1);
    expect(result).toMatchObject({ pending: false, connections: [{ fromNodeId: "source", toNodeId: "target" }] });
});

test("batch invalid card reports its policy reason without creating a node", async () => {
    await start(1, true, true);
    await move(760, 280);
    await page.mouse.up();
    expect(await state()).toMatchObject({ pending: false, connections: [] });
    await page.getByText("图片生成节点不能连接参考视频", { exact: true }).waitFor();
});

test("reference shelf replacement keeps precedence over overlay blocking and nearby cards", async () => {
    await start(1);
    // Empty shelf space selects the closest existing reference card.
    await move(1450, 150);
    expect(await page.locator('[data-reference-id="reference-1"]').getAttribute("data-connection-hover-replace")).toBe("true");
    await page.mouse.up();
    expect(await state()).toMatchObject({ pending: false, connections: [], replacement: "reference-1" });
});
