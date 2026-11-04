import { expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { DreamaVoiceChoices, DreamaVoiceBindingButton } from "@/components/canvas/canvas-image-voice-dialog";
import type { DreamaVoiceCatalogItem } from "@/types/dreama-voice";

test("the picture entry shows binding state and the selected voice without hiding the action", () => {
    expect(renderToStaticMarkup(<DreamaVoiceBindingButton onClick={() => {}} />)).toContain("绑定音色");
    const bound = renderToStaticMarkup(<DreamaVoiceBindingButton binding={{ version: 1, name: "旁白原声", source: "catalog", voiceId: "23" }} onClick={() => {}} />);
    expect(bound).toContain("旁白原声");
    expect(bound).toContain('aria-pressed="true"');
});

test("voice choices distinguish personal voices and never autoplay while merely browsing", () => {
    const voices: DreamaVoiceCatalogItem[] = [{ id: "23", name: "我的旁白", source: "personal", previewUrl: "https://cdn.example/voice.wav" }, { id: "24", name: "公共示例", source: "public", previewUrl: "" }];
    const html = renderToStaticMarkup(<DreamaVoiceChoices voices={voices} selectedId="23" onSelect={() => {}} onPreview={() => {}} />);
    expect(html).toContain("我的音色");
    expect(html).toContain("公共音色");
    expect(html).toContain('aria-pressed="true"');
    expect(html).not.toContain("<audio");
    expect(html).not.toContain("https://cdn.example");
    expect(html).toContain('aria-label="试听 公共示例" disabled');
});
