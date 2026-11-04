import { expect, test } from "bun:test";

test("assistant production hides unsupported Dreama confirmation and blocks direct handler calls", async () => {
    const child = Bun.spawn([process.execPath, "run", "test/fixtures/assistant-production-support.fixture.tsx"], { cwd: import.meta.dir + "/..", stdout: "pipe", stderr: "pipe" });
    const [status, stdout, stderr] = await Promise.all([child.exited, new Response(child.stdout).text(), new Response(child.stderr).text()]);
    expect({ status, stderr }).toEqual({ status: 0, stderr: "" });
    expect(stdout).toContain("PASS: Dreama production confirmation is absent and direct callbacks cannot submit");
    expect(stdout).toContain("PASS: ordinary Dreama proposals and other supported production proposals retain their paths");
}, 15000);
