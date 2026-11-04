import { expect, test } from "bun:test";
import { dreamaImageToolSupport } from "../src/lib/canvas/dreama-image-tool-support";
import { createModelChannel, defaultConfig, encodeChannelModel } from "../src/stores/use-config-store";
import { defaultModelCapabilityConfig } from "../src/lib/model-capabilities";
import { bindDreamaProject } from "../src/lib/canvas/dreama-workbench";
import { CanvasNodeType, type CanvasNodeData } from "../src/types/canvas";

function fixture() {
    const image = defaultModelCapabilityConfig("openai-image").image!;
    image.size = { parameter: "aspect_ratio", values: ["1:1"], default: "1:1", allowCustom: false };
    image.quality = { supported: true, values: ["3K"], default: "3K" };
    image.references = { ...image.references, maxImages: 3, maskSupported: false };
    const channel = createModelChannel({ id: "dreama-image", enabled: true, baseUrl: "http://127.0.0.1:18765", apiKey: "fixture-only", interfaceType: "dreama-images-async", models: ["image"], modelProfiles: [{ model: "image", capability: "image", protocol: "dreama-images-async", imageCapabilitiesVersion: "v1", capabilityConfig: { version: 1, image } }] });
    const config = { ...defaultConfig, channels: [channel], model: encodeChannelModel(channel.id,"image"), imageModel: encodeChannelModel(channel.id,"image"), quality: "3K", size: "1:1", count: "1" };
    const node: CanvasNodeData = { id: "source", type: CanvasNodeType.Image, title: "人物", position: { x: 0, y: 0 }, width: 100, height: 100, metadata: { content: "data:image/png;base64,AA==", model: config.imageModel, quality: "3K", size: "1:1" } };
    const canvas = { id: "canvas", nodes: [node], integrations: { dreama: bindDreamaProject(undefined, { id: 41, name: "项目", promptMode: "drama", aspectRatio: "1:1" }, 12) } };
    return { config, node, canvas, channel };
}

test("unsupported Dreama pixel editing stays hidden even if a stale profile claims masks", () => {
    const { config,node,canvas,channel }=fixture();
    channel.modelProfiles![0]!.capabilityConfig!.image!.references.maskSupported=true;
    node.metadata!.quality = "8K";
    for (const id of ["maskEdit", "emotion"]) expect(dreamaImageToolSupport(id,node,config,canvas)).toMatchObject({disabled:true,hidden:true});
});
test("reference editing is ready only for existing supported model and resolved source group", () => {
    const { config,node,canvas }=fixture();
    for(const id of ["angle","lighting","portraitTexture"]) expect(dreamaImageToolSupport(id,node,config,canvas)).toEqual({disabled:false});
    node.parentId="missing";
    expect(dreamaImageToolSupport("angle",node,config,canvas)?.reason).toContain("分组");
});
test("recoverable binding, source, channel and stale parameters explain before submission", () => {
    const { config,node,canvas,channel }=fixture();
    expect(dreamaImageToolSupport("angle",node,config)?.reason).toContain("画布尚未加载");
    expect(dreamaImageToolSupport("angle",node,config,{...canvas,integrations:undefined})?.reason).toContain("绑定");
    node.metadata.content="";
    expect(dreamaImageToolSupport("angle",node,config,canvas)?.disabled).toBe(true);
    node.metadata.content="source";node.metadata.quality="8K";
    expect(dreamaImageToolSupport("angle",node,config,canvas)?.reason).toContain("分辨率");
    node.metadata.quality="3K";channel.enabled=false;
    expect(dreamaImageToolSupport("angle",node,config,canvas)?.disabled).toBe(true);
});
test("unsupported reference capability and removed catalog profile cannot fall back to defaults", () => {
    const { config,node,canvas,channel }=fixture();
    channel.modelProfiles![0]!.capabilityConfig!.image!.references.maxImages=0;
    expect(dreamaImageToolSupport("angle",node,config,canvas)?.reason).toContain("参考图");
    channel.modelProfiles=[];
    expect(dreamaImageToolSupport("angle",node,config,canvas)?.disabled).toBe(true);
});
test("local tools and other configured providers retain their existing behavior", () => {
    const { config,node,canvas,channel }=fixture();
    for(const id of ["crop","annotation","split","upscale","view"]) expect(dreamaImageToolSupport(id,node,config,canvas)).toBeUndefined();
    channel.interfaceType="openai-image";channel.modelProfiles![0]!.protocol="openai-image";
    expect(dreamaImageToolSupport("maskEdit",node,config,canvas)).toBeUndefined();
});

test("tool drafts use their original image while validating the target episode", () => {
    const { config, node, canvas } = fixture();
    const draft: CanvasNodeData = { ...node, id: "draft", metadata: { ...node.metadata, content: undefined, imageTool: { version: 1, kind: "angle", sourceNodeId: node.id } } };
    canvas.nodes.push(draft);
    expect(dreamaImageToolSupport("angle", draft, config, canvas, "generation")).toEqual({ disabled: false });
    draft.parentId = "missing";
    expect(dreamaImageToolSupport("angle", draft, config, canvas, "generation")?.reason).toContain("分组");
    draft.parentId = undefined;
    canvas.nodes = [draft];
    expect(dreamaImageToolSupport("angle", draft, config, canvas, "generation")?.reason).toContain("原图");
    draft.metadata!.content = "completed-tool-output";
    expect(dreamaImageToolSupport("lighting", draft, config, canvas)).toEqual({ disabled: false });
});

test("a removed explicit channel cannot fall back to another provider's editing tools", () => {
    const { config, node, canvas } = fixture();
    config.channels = [createModelChannel({ id: "other", enabled: true, apiKey: "test", interfaceType: "openai-image", models: ["other-image"] })];
    for (const id of ["maskEdit", "emotion", "angle", "lighting", "portraitTexture"]) {
        expect(dreamaImageToolSupport(id, node, config, canvas)).toMatchObject({ disabled: true });
        expect(dreamaImageToolSupport(id, node, config, canvas)?.reason).toContain("已不可用");
    }
});

test("recoverable endpoint and credential readiness block tools, with the declared no-auth exception", () => {
    const { config, node, canvas, channel } = fixture();
    channel.apiKey = "";
    expect(dreamaImageToolSupport("angle", node, config, canvas)).toMatchObject({ disabled: true });
    channel.modelProfiles![0]!.authType = "none";
    expect(dreamaImageToolSupport("angle", node, config, canvas)).toEqual({ disabled: false });
    channel.baseUrl = "";
    expect(dreamaImageToolSupport("angle", node, config, canvas)).toMatchObject({ disabled: true });
});
