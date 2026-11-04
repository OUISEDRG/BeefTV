import { expect, test } from "bun:test";

test("image tools use the real Dreama coordinator with authored references and frozen routing", async () => {
    const child = Bun.spawn([process.execPath, "run", "test/fixtures/dreama-image-tool-generation.fixture.ts"], {
        cwd: import.meta.dir + "/..", stdout: "pipe", stderr: "pipe",
    });
    const [status, stdout, stderr] = await Promise.all([child.exited, new Response(child.stdout).text(), new Response(child.stderr).text()]);
    expect({ status, stderr }).toEqual({ status: 0, stderr: "" });
    expect(stdout).toContain("PASS: Codex video reverse handler blocks before node writes and image reverse stays available");
    expect(stdout).toContain("PASS: angle uses the coordinator and freezes the source episode before asynchronous preparation");
    expect(stdout).toContain("PASS: lighting and portrait drafts submit their visible authored prompts without hidden additions");
    expect(stdout).toContain("PASS: unsupported direct edits and stale tool metadata cannot dispatch");
    expect(stdout).toContain("PASS: missing or extra references stop before dispatch");
}, 15000);
