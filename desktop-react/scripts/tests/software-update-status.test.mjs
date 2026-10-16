import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/components/softwareUpdateStatus.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { statusCopy } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const base = { enabled: true, phase: "idle", currentVersion: "0.1.91", automaticUpdates: false };

test("idle Host and disabled automatic checks never claim an update is in progress", () => {
  const copy = statusCopy({ ...base, hostRuntime: { enabled: true, phase: "idle" } });
  assert.equal(copy.title, "Automatic updates are off");
  assert.equal(copy.tone, "neutral");
  assert.equal(statusCopy({ ...base, automaticUpdates: true, hostRuntime: { enabled: true, phase: "idle" } }).title, "Automatic updates are on");
});

test("Host progress is displayed only for real work, and does not mask application downloads", () => {
  assert.equal(statusCopy({ ...base, hostRuntime: { enabled: true, phase: "checking" } }).tone, "active");
  assert.equal(statusCopy({ ...base, hostRuntime: { enabled: true, phase: "error", error: "network unavailable" } }).detail, "network unavailable");
  assert.equal(statusCopy({ ...base, phase: "downloading", availableVersion: "0.1.92", hostRuntime: { enabled: true, phase: "checking" } }).title, "Downloading Loom 0.1.92");
});

test("account verification failure and unsupported builds have distinct explanations", () => {
  assert.equal(statusCopy({ ...base, phase: "disabled", accountUpdatesAllowed: false }).title, "Update access could not be verified");
  assert.equal(statusCopy({ ...base, enabled: false, phase: "disabled" }).title, "Updates are unavailable in this build");
});

test("a blocked restart keeps the downloaded update and explains why installation must wait", () => {
  const copy = statusCopy({ ...base, phase: "downloaded", availableVersion: "0.1.92", error: "Wait for running tasks" });
  assert.equal(copy.detail, "Wait for running tasks");
  assert.equal(copy.tone, "warning");
});
