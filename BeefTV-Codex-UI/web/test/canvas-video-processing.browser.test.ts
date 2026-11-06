import { afterAll, beforeAll, expect, test } from "bun:test";
import { existsSync } from "node:fs";
import { chromium, type Browser } from "playwright";

let browser: Browser;
let server: ReturnType<typeof Bun.serve>;
let posts = 0;
beforeAll(async () => {
    const build = await Bun.build({ entrypoints: [import.meta.dir + "/fixtures/canvas-video-processing-harness.tsx"], target: "browser", define: {
        "import.meta.env.DEV": "false", "import.meta.env.PROD": "true", "import.meta.env.MODE": '"production"', "import.meta.env.VITE_CANVAS_LOCAL_MODE": '"true"', "import.meta.env.VITE_CANVAS_BACKEND_URL": '"/api"', "process.env.NODE_ENV": '"production"',
    }, plugins: [{ name: "source", setup(builder) {
        builder.onResolve({ filter: /^@\/components\/model-logo$/ }, () => ({ path: "model-logo", namespace: "test-icons" }));
        builder.onLoad({ filter: /.*/, namespace: "test-icons" }, () => ({ contents: "export function ModelLogo() { return null; }", loader: "js" }));
        builder.onResolve({ filter: /^@\// }, args => ({ path: Bun.resolveSync("../src/" + args.path.slice(2), import.meta.dir) }));
    } }] });
    if (!build.success) throw new Error(build.logs.join("\n"));
    const js = await build.outputs.find(output => output.path.endsWith(".js"))!.text();
    server = Bun.serve({ port: 0, fetch(request) {
        const path = new URL(request.url).pathname;
        if (request.method === "POST") posts++;
        if (path === "/harness.js") return new Response(js, { headers: { "Content-Type": "application/javascript" } });
        if (path.startsWith("/api/")) return Response.json({ code: 0, data: [], msg: "ok" });
        return new Response('<html><meta name="viewport" content="width=device-width"><style>body{margin:0}.canvas-node-toolbar{position:absolute}.canvas-node-toolbar [role="toolbar"]{display:flex;white-space:nowrap}.canvas-node-toolbar button{height:32px}.canvas-node-toolbar svg{width:14px;height:14px}output{display:block;overflow-wrap:anywhere}section{min-height:300px}.canvas-model-picker-popover{max-width:90vw}</style><body><div id="root"></div><script type="module" src="/harness.js"></script></body></html>', { headers: { "Content-Type": "text/html" } });
    } });
    const executablePath = [process.env.CHROME_PATH, "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].find((path): path is string => Boolean(path && existsSync(path)));
    browser = await chromium.launch({ executablePath, headless: true });
}, 60000);
afterAll(async () => { server?.stop(true); await browser?.close(); }, 30000);

for (const width of [1200, 390]) test(`video processing menu and enhancement composer work at ${width}px without submitting`, async () => {
    const page = await browser.newPage({ viewport: { width, height: 1000 } });
    page.setDefaultTimeout(5000);
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    try {
        await page.goto(server.url.toString());
        await page.getByRole("button", { name: "视频处理", exact: true }).click();
        for (const label of ["视频剪辑", "画面裁切", "深度动作捕捉", "视频延长", "视频增强", "反推提示词"]) await page.getByRole("menuitem", { name: label, exact: true }).waitFor();
        expect(await page.getByRole("button", { name: "视频二创", exact: true }).count()).toBe(0);
        expect(await page.getByText("进入剪辑", { exact: true }).count()).toBe(0);
        await page.getByRole("menuitem", { name: "视频增强", exact: true }).click();
        await page.getByRole("region", { name: "处理节点配置" }).waitFor();
        await page.getByRole("button", { name: "高级设置：随原视频时长", exact: true }).waitFor();
        await page.getByRole("button", { name: /增强方式 1/ }).first().waitFor();
        await page.getByRole("button", { name: /增强方式 1/ }).first().click();
        await page.getByRole("button", { name: "处理测试渠道", exact: true }).click();
        await page.getByRole("option", { name: /增强方式 2/ }).waitFor();
        await page.getByRole("option", { name: /增强方式 2/ }).click();
        await page.waitForFunction(() => document.querySelector('[data-testid="draft"]')?.textContent?.includes('processing::enhance-two'));
        const draft = JSON.parse(await page.getByTestId("draft").textContent() || "{}");
        expect(draft.node.parentId).toBe("episode");
        expect(draft.node.metadata.videoEditOperation).toBe("upscale");
        expect(draft.connection.fromNodeId).toBe("source");
        expect(draft.node.metadata.taskId).toBeUndefined();
        expect(await page.getByTestId("submissions").textContent()).toBe("0");
        expect(posts).toBe(0);
        expect(errors).toEqual([]);
    } catch (error) {
        throw new Error(`${error}\nBrowser errors: ${errors.join("\n")}\nDOM: ${(await page.locator("body").innerText()).slice(0, 3000)}`);
    } finally { await page.close(); }
}, 60000);
