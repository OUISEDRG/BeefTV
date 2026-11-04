import { expect, test } from "bun:test";

test("real Dreama coordinator preserves body, final voice indices and frozen routing without leaking mocks", async () => {
    const child = Bun.spawn([process.execPath, "run", "test/fixtures/dreama-generation-pipeline.fixture.ts"], {
        cwd: import.meta.dir + "/..", stdout: "pipe", stderr: "pipe",
    });
    const [status, stdout, stderr] = await Promise.all([child.exited, new Response(child.stdout).text(), new Response(child.stderr).text()]);
    expect({ status, stderr }).toEqual({ status: 0, stderr: "" });
    expect(stdout).toContain("PASS: real character hydration expands only markers while ordinary speaker voice follows final indices");
    expect(stdout).toContain("PASS: Dreama image and audio coordinator preserve authored text instead of appending connected text");
    expect(stdout).toContain("PASS: real canvas pipeline previews without dispatch, freezes group binding and merges compiled voice indices");
}, 15000);
