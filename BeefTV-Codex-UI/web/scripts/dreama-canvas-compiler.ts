import { compileDreamaCanvas } from "../src/lib/canvas/dreama-canvas-compiler";

const chunks: Buffer[] = [];
let length = 0;
try {
    for await (const chunk of process.stdin) {
        length += chunk.length;
        if (length > 8 * 1024 * 1024) throw new Error("画布超过单次准备大小限制");
        chunks.push(chunk);
    }
    const result = await compileDreamaCanvas(JSON.parse(Buffer.concat(chunks).toString("utf8")));
    process.stdout.write(JSON.stringify(result));
} catch (error) {
    process.stdout.write(JSON.stringify({ error: { reason: "generation_prepare_invalid", message: error instanceof SyntaxError ? "画布准备输入格式无效" : error instanceof Error ? error.message : "画布准备失败" } }));
    process.exitCode = 1;
}
