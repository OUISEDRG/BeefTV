import { expect, test } from "bun:test";

test("Dreama batch retries retain original task ownership and only dispatch known-unsubmitted outputs", async () => {
    const child = Bun.spawn([process.execPath, "run", "test/fixtures/dreama-image-retry.fixture.ts"], { cwd: import.meta.dir + "/..", stdout: "pipe", stderr: "pipe" });
    const [status, stdout, stderr] = await Promise.all([child.exited, new Response(child.stdout).text(), new Response(child.stderr).text()]);
    expect({ status, stderr }).toEqual({ status: 0, stderr: "" });
    expect(stdout).toContain("PASS: actual image executor retries only the missing single output with original references and episode");
    expect(stdout).toContain("PASS: stored native tasks reconcile once, preserve completed outputs and never turn recovery into paid retries");
    expect(stdout).toContain("PASS: completed batch outputs retain their parent episode when used by the next tool");
    expect(stdout).toContain("PASS: task-id and operation-id aliases share one retry and partial native failures never replace completed siblings");
}, 15000);
