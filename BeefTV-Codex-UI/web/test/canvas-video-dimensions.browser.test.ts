import { afterAll, beforeAll, expect, test } from "bun:test";
import { existsSync } from "node:fs";
import { chromium, type Browser, type Page } from "playwright";
import type { CanvasNodeMetadata } from "../src/types/canvas";

let browser: Browser;
let script: string;
beforeAll(async () => {
    const build = await Bun.build({ entrypoints: [import.meta.dir + "/fixtures/canvas-video-dimensions-harness.tsx"], target: "browser", format: "iife",
        define: { "process.env.NODE_ENV": '"production"', "import.meta.env": "{}" },
        plugins: [{ name: "video-decode-boundary", setup(builder) {
            builder.onResolve({ filter: /^@\// }, args => ({ path: Bun.resolveSync("../src/" + args.path.slice(2), import.meta.dir) }));
            builder.onLoad({ filter: /\.(css|svg|png|jpe?g|gif|webp|woff2?)$/ }, () => ({ contents: "export default ''", loader: "js" }));
            builder.onLoad({ filter: /[\\/]components[\\/]model-logo\.tsx$/ }, () => ({ contents: "export const ModelLogo=()=>null;", loader: "js" }));
            // Control decoder metadata without loading a real file or invoking the resource backend.
            builder.onLoad({ filter: /[\\/]components[\\/]video-player\.tsx$/ }, () => ({ contents: 'import {createElement} from "react"; export const VideoPlayer=()=>createElement("video");', loader: "js" }));
            builder.onLoad({ filter: /[\\/]hooks[\\/]use-resource-video-playback\.ts$/ }, () => ({ contents: 'const noop=()=>{}; export const useResourceVideoPlayback=(key,fallback,active=true)=>({url:active?"fixture-video":"",loading:false,error:"",compatible:window.videoDimensionsFixture?.compatible||false,requestCompatible:()=>false,retry:noop,retryEpoch:0,fail:noop});', loader: "js" }));
            builder.onLoad({ filter: /[\\/]services[\\/]canvas-video-preview\.ts$/ }, () => ({ contents: 'export const CANVAS_VIDEO_PREVIEW_VERSION=2; export const canvasDerivedPreviewSourceKey=()=>"fixture"; export const canvasVideoPreviewNeedsHydration=()=>false; export const hasCachedCanvasVideoPreview=()=>false; export const acquireCanvasVideoPreview=()=>({promise:Promise.resolve(null),release:()=>{}});', loader: "js" }));
        } }],
    });
    if (!build.success) throw new Error(build.logs.join("\n"));
    script = await build.outputs[0].text();
    const executablePath = [process.env.CHROME_PATH, chromium.executablePath(), "C:/Program Files/Google/Chrome/Application/chrome.exe", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"].find(item => item && existsSync(item));
    browser = await chromium.launch({ headless: true, executablePath });
}, 30_000);
afterAll(async () => { await browser?.close(); });

async function withVideo(metadata: CanvasNodeMetadata, run: (page: Page) => Promise<void>, geometry = { width: 720, height: 405 }, compatible = false, active = true) {
    const page = await browser.newPage();
    const errors: string[] = [];
    page.on("pageerror", error => { errors.push(error.message); console.error(error.message); });
    await page.route(/^https?:\/\//, route => route.abort());
    try {
        await page.route("http://canvas-video.test/", route => route.fulfill({ contentType: "text/html; charset=utf-8", body: `<meta charset="utf-8"><div id="root"></div><script type="module">${script.replaceAll("</script", "<\\/script")}</script>` }));
        await page.goto("http://canvas-video.test/");
        await page.locator("video").waitFor({ state: "attached", timeout: 3000 });
        await page.evaluate(({ metadata, geometry, compatible, active }) => {
            const fixture = window.videoDimensionsFixture;
            fixture.compatible = compatible;
            fixture.setActive(active);
            fixture.setNode({ ...fixture.node, ...geometry, metadata: { ...fixture.node.metadata, ...metadata } });
        }, { metadata, geometry, compatible, active });
        await settle(page);
        await run(page);
        expect(errors).toEqual([]);
    } finally { await page.close(); }
}

test("known portrait video metadata corrects the result frame before playback is activated", async () => {
    await withVideo({ naturalWidth: 720, naturalHeight: 1280 }, async page => {
        expect(await page.locator("video").count()).toBe(0);
        const result = await page.evaluate(() => window.videoDimensionsFixture.node);
        expect(result.width / result.height).toBeCloseTo(9 / 16, 5);
    }, { width: 720, height: 405 }, false, false);
});

async function settle(page: Page) { await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))); }
async function decode(page: Page, width = 1080, height = 1920) {
    await page.locator("video").evaluate((video, { width, height }) => {
        Object.defineProperties(video, { videoWidth: { configurable: true, value: width }, videoHeight: { configurable: true, value: height } });
        video.dispatchEvent(new Event("loadedmetadata"));
    }, { width, height });
    await settle(page);
    return page.evaluate(() => window.videoDimensionsFixture.node);
}

for (const metadataKnown of [false, true]) {
    test(`video decode fixes a landscape result box when portrait metadata is ${metadataKnown ? "already known" : "new"}`, async () => {
        await withVideo(metadataKnown ? { naturalWidth: 1080, naturalHeight: 1920 } : {}, async page => {
            const result = await decode(page);
            expect(result.width / result.height).toBeCloseTo(9 / 16, 5);
            expect(result.width).toBeCloseTo(405 * 9 / 16);
            expect(result.height).toBe(405);
            expect(result.metadata).toMatchObject({ naturalWidth: 1080, naturalHeight: 1920 });
            expect(result.position).toEqual({ x: 100, y: 200 });
            const writes = await page.evaluate(() => window.videoDimensionsFixture.writes);
            await decode(page);
            expect(await page.evaluate(() => window.videoDimensionsFixture.writes)).toBe(writes);
        });
    });
}

test("late landscape decode fits inside a saved portrait card without covering neighbors", async () => {
    await withVideo({}, async page => {
        const result = await decode(page, 1920, 1080);
        expect([result.width, result.height, result.position]).toEqual([420, 236.25, { x: 100, y: 200 }]);
        const writes = await page.evaluate(() => window.videoDimensionsFixture.writes);
        await decode(page, 1920, 1080);
        expect(await page.evaluate(() => window.videoDimensionsFixture.writes)).toBe(writes);
    }, { width: 420, height: 746 });
});

for (const [width, height, naturalWidth, naturalHeight] of [[270, 480, 1080, 1920], [320, 180, 1920, 1080]]) {
    test(`video decode preserves a ${width}x${height} result that already has the right ratio`, async () => {
        await withVideo({}, async page => {
            const result = await decode(page, naturalWidth, naturalHeight);
            expect([result.width, result.height, result.position]).toEqual([width, height, { x: 100, y: 200 }]);
            expect(result.metadata).toMatchObject({ naturalWidth, naturalHeight });
        }, { width, height });
    });
}

for (const metadata of [{ manualSize: true }, { freeResize: true }, { locked: true }, { workflowSlot: { slotId: "slot" } }]) {
    test(`video decode preserves geometry owned by ${Object.keys(metadata)[0]}`, async () => {
        await withVideo(metadata as CanvasNodeMetadata, async page => {
            const result = await decode(page);
            expect([result.width, result.height, result.position]).toEqual([720, 405, { x: 100, y: 200 }]);
            expect(result.metadata).toMatchObject({ naturalWidth: 1080, naturalHeight: 1920 });
        });
    });
}

test("compatible playback does not replace the original video dimensions with proxy dimensions", async () => {
    await withVideo({ naturalWidth: 1080, naturalHeight: 1920 }, async page => {
        const before = await page.evaluate(() => window.videoDimensionsFixture.node);
        expect(await decode(page, 640, 360)).toEqual(before);
    }, { width: 270, height: 480 }, true);
});

test("undecoded video leaves saved node geometry and metadata unchanged", async () => {
    await withVideo({}, async page => {
        const before = await page.evaluate(() => window.videoDimensionsFixture.node);
        expect(await decode(page, 0, 0)).toEqual(before);
    });
});
