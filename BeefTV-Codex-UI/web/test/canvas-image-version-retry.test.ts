import { expect, test } from "bun:test";

test("version retries keep single-output slots and reconcile the visible history node", async () => {
    const child = Bun.spawn([process.execPath, "run", "test/fixtures/canvas-image-version-retry.fixture.ts"], { cwd: import.meta.dir + "/..", stdout: "pipe", stderr: "pipe" });
    const [status, stdout, stderr] = await Promise.all([child.exited, new Response(child.stdout).text(), new Response(child.stderr).text()]);
    expect({ status, stderr }).toEqual({ status: 0, stderr: "" });
    expect(stdout).toContain("PASS: missing single-output slots reuse their identities and keep successful history");
    expect(stdout).toContain("PASS: root retry is sequential and duplicate slot retry is suppressed");
    expect(stdout).toContain("PASS: failed retries preserve submission identity and reconcile root failure");
}, 15000);
