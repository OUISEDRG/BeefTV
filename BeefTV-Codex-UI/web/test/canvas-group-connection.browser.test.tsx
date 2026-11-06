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
    const build = await Bun.build({ entrypoints: [import.meta.dir + "/fixtures/canvas-group-connection-harness.tsx"], target: "browser", format: "esm", define: { "import.meta.env": "{}", "process.env.NODE_ENV": '"production"' },
        plugins: [{ name: "alias", setup(builder) {
            builder.onLoad({ filter: /src[\\/]components[\\/]model-logo\.tsx$/ }, () => ({ contents: "export function ModelLogo() { return null; }", loader: "js" }));
            builder.onLoad({ filter: /\.(css|svg|png|jpe?g|gif|webp|woff2?)$/ }, () => ({ contents: "export default ''", loader: "js" }));
            builder.onResolve({ filter: /^@\// }, args => ({ path: Bun.resolveSync("../src/" + args.path.slice(2), import.meta.dir) }));
        } }] });
    if (!build.success) throw new Error(build.logs.join("\n"));
    const script = await build.outputs.find(item => item.path.endsWith(".js"))!.text();
    const cssPath = resolve(import.meta.dir, "../src/styles/globals.css");
    const css = (await postcss([tailwind({ base: resolve(import.meta.dir, "..") })]).process(await Bun.file(cssPath).text(), { from: cssPath })).css;
    const executablePath = [process.env.CHROME_PATH, "C:/Program Files/Google/Chrome/Application/chrome.exe"].find((path): path is string => Boolean(path && existsSync(path)));
    browser = await chromium.launch({ headless: true, executablePath });
    page = await browser.newPage({ viewport: { width: 1700, height: 900 } });
    page.setDefaultTimeout(5000);
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/*", route => {
        const url = new URL(route.request().url());
        if (url.origin !== "https://beeftv-group.test") return route.abort();
        if (url.pathname === "/app.js") return route.fulfill({ contentType: "text/javascript", body: script });
        if (url.pathname === "/style.css") return route.fulfill({ contentType: "text/css", body: css });
        return route.fulfill({ contentType: "text/html", body: '<html class="dark"><head><link rel="stylesheet" href="/style.css"></head><body style="margin:0"><div id="root"></div><script type="module" src="/app.js"></script></body></html>' });
    });
}, 60_000);
afterAll(async () => { await browser?.close(); });

async function state() { return JSON.parse(await page.locator("#state").textContent() || "{}"); }
async function load(query = "") {
    await page.goto(`https://beeftv-group.test/?${query}`);
    await page.locator('[data-node-id="group"] [data-canvas-group-output]').waitFor();
}
async function down(selector = '[data-node-id="group"] [data-canvas-group-output]') {
    const rect = await page.locator(selector).boundingBox();
    await page.mouse.move(rect!.x + rect!.width / 2, rect!.y + rect!.height / 2);
    await page.mouse.down();
}
async function move(x: number, y: number) {
    await page.mouse.move(x, y, { steps: 4 });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}
function expectThreeReferences(result: Awaited<ReturnType<typeof state>>, target = "target") {
    expect(result.connections.map((edge: any) => [edge.fromNodeId, edge.toNodeId])).toEqual([["asset-0", target], ["asset-1", target], ["asset-2", target]]);
    expect(result.pending).toBeNull();
    expect(result.moved).toBeNull();
    expect(result.interactions).toBeGreaterThan(0);
}

for (const query of ["scale=1", "scale=0.42&kind=storyboard", "scale=0.42&kind=reference_set&collapsed&nested"]) {
    test(`real group output connects three materials to a target body (${query})`, async () => {
        await load(query);
        const scale = Number(new URLSearchParams(query).get("scale"));
        await down();
        await move(1300 * scale, 250 * scale);
        expect((await state()).target).toBe("target");
        await page.mouse.up();
        expectThreeReferences(await state());
        await down();
        await move(1300 * scale, 250 * scale);
        await page.mouse.up();
        expect((await state()).connections).toHaveLength(3);
    });
}

test("reverse drag from a video input accepts the collapsed group body", async () => {
    await load("scale=0.42&collapsed&nested");
    await down('[data-node-id="target"] [data-canvas-connection-rail="left"]');
    await move(430 * 0.42, 330 * 0.42);
    expect((await state()).target).toBe("group");
    await page.mouse.up();
    expectThreeReferences(await state());
});

test("empty canvas opens the real group create menu and creates three real input edges", async () => {
    await load("collapsed&nested");
    await down();
    await move(1200, 650);
    await page.mouse.up();
    await page.getByText("引用整组素材生成", { exact: true }).waitFor();
    expect((await state()).pending.groupSourceNodeId).toBe("group");
    await page.locator("[data-connection-create-menu]").getByRole("button", { name: "视频", exact: true }).click();
    const result = await state();
    const created = result.nodeIds.find((id: string) => !["group", "nested", "target", "asset-0", "asset-1", "asset-2"].includes(id));
    expect(created).toBeTruthy();
    expectThreeReferences(result, created);
});

test("invalid target reports the reason and never opens a create menu", async () => {
    await load("invalid");
    await down();
    await move(1300, 250);
    await page.mouse.up();
    expect(await state()).toMatchObject({ connections: [], pending: null });
    await page.getByText("音频生成节点只接受文本或单个角色卡输入", { exact: true }).waitFor();
    await page.getByRole("button", { name: "关闭连线提示", exact: true }).click();
    expect(await page.locator("[data-canvas-connection-feedback]").count()).toBe(0);
});

test("a forward drop on the group body explains the unsupported target without making a virtual edge", async () => {
    await load();
    await down('[data-node-id="target"] [data-canvas-connection-rail="right"]');
    await move(810, 530);
    await page.mouse.up();
    expect(await state()).toMatchObject({ connections: [], pending: null });
    await page.getByRole("alert").getByText("组不能作为引用目标，请连接到组内具体节点", { exact: true }).waitFor();
});

test("an empty group explains why it cannot start and cancellation clears all group state", async () => {
    await load("empty");
    await down();
    await page.mouse.up();
    await page.getByText("组内没有可引用的素材", { exact: true }).waitFor();
    expect(await state()).toMatchObject({ connections: [], pending: null, batch: null });
    await load();
    await down();
    await move(1200, 650);
    await page.mouse.up();
    await page.locator("#cancel").click();
    expect(await state()).toMatchObject({ connections: [], pending: null, batch: null });
    expect(errors).toEqual([]);
});
