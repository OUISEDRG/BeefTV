import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { CanvasQuickConnectControl } from "@/components/canvas/canvas-quick-connect-control";
import { canvasThemes } from "@/lib/canvas-theme";

describe("quick connection control accessibility", () => {
    test("exposes an unpressed toggle before the mode starts", () => {
        const html = renderToStaticMarkup(<CanvasQuickConnectControl active={false} onToggle={() => {}} theme={canvasThemes.light} />);
        expect(html).toContain('aria-label="快速连线"');
        expect(html).toContain('aria-pressed="false"');
        expect(html).not.toContain('role="status"');
    });

    test("announces the active mode and both exit methods", () => {
        const html = renderToStaticMarkup(<CanvasQuickConnectControl active onToggle={() => {}} theme={canvasThemes.dark} />);
        expect(html).toContain('aria-pressed="true"');
        expect(html).toContain('aria-describedby=');
        expect(html).toContain('role="status"');
        expect(html).toContain("点击素材或组可连续引用");
        expect(html).toContain("按 Esc 或再次点击退出");
    });
});
