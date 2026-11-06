import { afterAll, beforeAll, expect, test } from "bun:test";
import { existsSync, readdirSync, readFileSync, mkdirSync } from "node:fs";
import { chromium, type Browser } from "playwright";

let browser: Browser;
let html: string;
beforeAll(async () => {
    const build = await Bun.build({ entrypoints: [import.meta.dir + "/fixtures/canvas-media-history-harness.tsx"], target: "browser", format: "iife",
        define: { "process.env.NODE_ENV": '"production"', "import.meta.env": "{}" },
        plugins: [{ name: "history-fixture", setup(builder) {
            builder.onResolve({ filter: /^@\// }, args => ({ path: Bun.resolveSync("../src/" + args.path.slice(2), import.meta.dir) }));
            builder.onLoad({ filter: /\.(css|svg|png|jpe?g|gif|webp|woff2?)$/ }, () => ({ contents: "export default ''", loader: "js" }));
        } }],
    });
    if (!build.success) throw new Error(build.logs.join("\n"));
    const styles = ["../dist/static", "../dist/assets", "../../backend/cmd/desktop/frontend/dist/assets"].map(path => import.meta.dir + "/" + path).find(path => existsSync(path));
    const css = styles ? readdirSync(styles).filter(name => name.endsWith(".css")).map(name => readFileSync(styles + "/" + name, "utf8")).join("\n") : "";
    html = `<meta charset="utf-8"><style>${css}</style><div id="root"></div><script>${(await build.outputs[0].text()).replaceAll("</script", "<\\/script")}</script>`;
    const executablePath = [process.env.CHROME_PATH, chromium.executablePath(), "C:/Program Files/Google/Chrome/Application/chrome.exe"].find(path => path && existsSync(path));
    browser = await chromium.launch({ headless: true, executablePath });
}, 30_000);
afterAll(async () => { await browser?.close(); });

test("right corner history opens, switches both ways, persists after reload, and closes with Escape", async () => {
    const page = await browser.newPage({ viewport: { width: 1180, height: 740 } });
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.route("http://canvas-history.test/", route => route.fulfill({ contentType: "text/html; charset=utf-8", body: html }));
    try {
        await page.goto("http://canvas-history.test/");
        const badge = page.getByRole("button", { name: "查看 2 个版本" });
        await badge.click();
        await page.getByRole("region", { name: "历史版本" }).waitFor();
        expect(await page.locator("[data-current-version]").textContent()).toBe("B");
        await page.getByRole("button", { name: "使用版本 v1" }).click();
        expect(await page.locator("[data-current-version]").textContent()).toBe("A");
        await page.getByRole("button", { name: "使用版本 v2" }).click();
        expect(await page.locator("[data-current-version]").textContent()).toBe("B");
        await page.getByRole("button", { name: "切换生成状态" }).click();
        await badge.click();
        expect(await page.getByRole("button", { name: "使用版本 v1" }).isDisabled()).toBe(true);
        await page.getByRole("button", { name: "切换生成状态" }).click();
        await badge.click();
        await page.keyboard.press("Escape");
        expect(await page.getByRole("region", { name: "历史版本" }).count()).toBe(0);
        await page.reload();
        await page.getByRole("button", { name: "查看 2 个版本" }).click();
        expect(await page.getByRole("button", { name: "使用版本 v1" }).count()).toBe(1);
        if (stylesAvailable()) {
            const owner = await page.locator("[data-fixture-owner]").boundingBox();
            const button = await badge.boundingBox();
            const history = await page.getByRole("region", { name: "历史版本" }).boundingBox();
            expect(button!.x).toBeGreaterThan(owner!.x + owner!.width / 2);
            expect(history!.x).toBeGreaterThan(owner!.x + owner!.width);
        }
        const output = process.env.BEEFTV_HISTORY_SCREENSHOT_DIR;
        if (output) { mkdirSync(output, { recursive: true }); await page.screenshot({ path: output + "/history-dark.png" }); }
        await page.getByRole("button", { name: "切换主题" }).click();
        await badge.click();
        if (output) await page.screenshot({ path: output + "/history-light.png" });
        expect(errors).toEqual([]);
    } finally { await page.close(); }
}, 30_000);

function stylesAvailable() { return existsSync(import.meta.dir + "/../dist/static"); }

test("four generated versions share one node, highlight current, switch and persist", async () => {
    const page = await browser.newPage({ viewport: { width: 1180, height: 740 } });
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.route("http://canvas-history.test/**", route => route.fulfill({ contentType: "text/html; charset=utf-8", body: html }));
    try {
        await page.goto("http://canvas-history.test/?batch=4");
        expect(await page.locator("[data-fixture-owner]").count()).toBe(1);
        await page.getByRole("button", { name: "查看 4 个版本" }).click();
        expect(await page.getByRole("region", { name: "历史版本" }).locator("article").count()).toBe(4);
        expect(await page.getByText("当前版本", { exact: true }).count()).toBe(1);
        const footerFits = await page.getByText("当前版本", { exact: true }).evaluate(element => {
            const footer = element.getBoundingClientRect();
            const card = element.closest("article")!.getBoundingClientRect();
            return footer.height > 0 && footer.bottom <= card.bottom;
        });
        expect(footerFits).toBe(true);
        expect(await page.locator("[data-current-version]").textContent()).toBe("D");
        await page.getByRole("button", { name: "使用版本 v1" }).click();
        expect(await page.locator("[data-current-version]").textContent()).toBe("A");
        await page.reload();
        await page.getByRole("button", { name: "查看 4 个版本" }).click();
        expect(await page.locator("[data-current-version]").textContent()).toBe("A");
        await page.getByRole("button", { name: "使用版本 v4" }).click();
        expect(await page.locator("[data-current-version]").textContent()).toBe("D");
        const persisted = await page.evaluate(() => JSON.parse(localStorage.getItem("history-batch-fixture") || "[]"));
        expect(persisted.filter((node: { metadata?: { taskId?: string } }) => node.metadata?.taskId).map((node: { id: string }) => node.id)).toEqual(["version-0", "version-1", "version-2", "version-3"]);
        if (process.env.BEEFTV_HISTORY_SCREENSHOT_DIR) await page.screenshot({ path: process.env.BEEFTV_HISTORY_SCREENSHOT_DIR + "/image-four-versions.png" });
        expect(errors).toEqual([]);
    } finally { await page.close(); }
}, 30_000);
