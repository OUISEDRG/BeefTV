import { expect, test } from "bun:test";

test("image generation produces one visible node with durable history versions", async () => {
    const child = Bun.spawn([process.execPath, "run", "test/fixtures/image-version-generation.fixture.ts"], { cwd: import.meta.dir + "/..", stdout: "pipe", stderr: "pipe" });
    const [status, stdout, stderr] = await Promise.all([child.exited, new Response(child.stdout).text(), new Response(child.stderr).text()]);
    expect({ status, stderr }).toEqual({ status: 0, stderr: "" });
    expect(stdout).toContain("PASS: adjustable counts");
}, 15000);
