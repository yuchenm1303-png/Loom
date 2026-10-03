import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { currentHostRuntime } from "./hostRuntime.js";

export const HOST_HEALTH_SCHEMA = 2;

export type HostHealthCapabilities = {
  browser: boolean;
  computerUse: boolean;
  terminal: boolean;
  files: boolean;
};

export type HostHealthSnapshot = {
  schema: number;
  osRelease: string;
  osVersion: string;
  arch: string;
  systemUptimeSeconds: number;
  cpuPercent: number | null;
  memoryTotalBytes: number;
  memoryUsedBytes: number;
  memoryPercent: number;
  diskTotalBytes: number | null;
  diskFreeBytes: number | null;
  diskPercent: number | null;
  hostRssBytes: number;
  hostHeapUsedBytes: number;
  hostCpuPercent: number | null;
  relayRttMs: number | null;
  capabilities: HostHealthCapabilities;
};

type CpuTotals = { idle: number; total: number };

let previousSystemCpu: CpuTotals | null = null;
let previousProcessCpu = process.cpuUsage();
let previousProcessAt = process.hrtime.bigint();

function rounded(value: number, digits = 1): number {
  const scale = 10 ** digits;
  return Math.round(value * scale) / scale;
}

function percent(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, rounded(value)));
}

function cpuTotals(): CpuTotals {
  let idle = 0;
  let total = 0;
  for (const cpu of os.cpus()) {
    idle += cpu.times.idle;
    total += cpu.times.user + cpu.times.nice + cpu.times.sys + cpu.times.idle + cpu.times.irq;
  }
  return { idle, total };
}

function systemCpuPercent(): number | null {
  const current = cpuTotals();
  const previous = previousSystemCpu;
  previousSystemCpu = current;
  if (!previous) return null;
  const totalDelta = current.total - previous.total;
  const idleDelta = current.idle - previous.idle;
  if (totalDelta <= 0) return null;
  return percent((1 - Math.max(0, idleDelta) / totalDelta) * 100);
}

function hostCpuPercent(): number | null {
  const currentAt = process.hrtime.bigint();
  const currentCpu = process.cpuUsage();
  const elapsedMicros = Number(currentAt - previousProcessAt) / 1000;
  const usedMicros = Math.max(0, currentCpu.user - previousProcessCpu.user)
    + Math.max(0, currentCpu.system - previousProcessCpu.system);
  previousProcessCpu = currentCpu;
  previousProcessAt = currentAt;
  if (elapsedMicros <= 0) return null;
  const logicalCpus = Math.max(1, os.cpus().length);
  return percent((usedMicros / (elapsedMicros * logicalCpus)) * 100);
}

function diskUsage(): { total: number | null; free: number | null; percent: number | null } {
  try {
    const stat = fs.statfsSync(os.homedir());
    const blockSize = Number(stat.bsize || 0);
    const total = Number(stat.blocks || 0) * blockSize;
    const free = Number(stat.bavail || stat.bfree || 0) * blockSize;
    if (!(total > 0) || free < 0) return { total: null, free: null, percent: null };
    return { total, free, percent: percent(((total - free) / total) * 100) };
  } catch {
    return { total: null, free: null, percent: null };
  }
}

function hostCapabilities(): HostHealthCapabilities {
  const runtime = currentHostRuntime();
  const root = runtime.root;
  return {
    browser: fs.existsSync(path.join(root, "browser-current-tab", "manifest.json")),
    computerUse: process.platform === "win32" && fs.existsSync(path.join(root, "wxc-exec.exe")),
    terminal: true,
    files: true,
  };
}

export function collectHostHealthSnapshot(relayRttMs: number | null = null): HostHealthSnapshot {
  const totalMemory = Math.max(0, os.totalmem());
  const freeMemory = Math.max(0, os.freemem());
  const usedMemory = Math.max(0, totalMemory - freeMemory);
  const memory = process.memoryUsage();
  const disk = diskUsage();
  const rtt = Number(relayRttMs);
  return {
    schema: HOST_HEALTH_SCHEMA,
    osRelease: String(os.release() || "").slice(0, 120),
    osVersion: String(typeof os.version === "function" ? os.version() : "").slice(0, 160),
    arch: String(process.arch || "").slice(0, 32),
    systemUptimeSeconds: Math.max(0, Math.floor(os.uptime())),
    cpuPercent: systemCpuPercent(),
    memoryTotalBytes: totalMemory,
    memoryUsedBytes: usedMemory,
    memoryPercent: totalMemory > 0 ? percent((usedMemory / totalMemory) * 100) : 0,
    diskTotalBytes: disk.total,
    diskFreeBytes: disk.free,
    diskPercent: disk.percent,
    hostRssBytes: Math.max(0, Number(memory.rss || 0)),
    hostHeapUsedBytes: Math.max(0, Number(memory.heapUsed || 0)),
    hostCpuPercent: hostCpuPercent(),
    relayRttMs: Number.isFinite(rtt) && rtt >= 0 ? Math.min(60_000, rounded(rtt)) : null,
    capabilities: hostCapabilities(),
  };
}
