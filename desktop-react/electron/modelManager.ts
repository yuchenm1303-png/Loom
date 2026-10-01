import { spawn, spawnSync } from "node:child_process";
import path from "node:path";

export interface ModelReasoningOption {
  value: string;
  label: string;
  description: string;
  advanced: boolean;
}

export interface ModelReasoningState {
  kind: "openai-effort" | "minimax-thinking" | string;
  value: string;
  defaultValue: string;
  options: ModelReasoningOption[];
  source: string;
}

export interface ModelProfile {
  selection: string;
  id: string;
  kind: "builtin" | "saved";
  name: string;
  adapter: "openai" | "openai-compatible" | string;
  baseUrl: string;
  model: string;
  groupId?: string;
  groupName?: string;
  groupOrder?: number;
  family?: string;
  protocol?: string;
  configured?: boolean;
  setupOnly?: boolean;
  statusMessage?: string;
  available?: boolean;
  catalogSource?: "provider" | "fallback" | "saved" | "runtime" | string;
  vision?: boolean;
  contextLimits?: {
    contextWindowTokens?: number;
    effectiveContextPercent?: number;
    autoCompactTokenLimit?: number;
    outputReserveTokens?: number;
    toolOutputTokenLimit?: number;
  };
  reasoning?: ModelReasoningState | null;
  authMode?: "loom-account" | string;
}

export interface ModelLaunchSpec extends ModelProfile {
  provider: string;
  apiKey: string;
}

export interface ModelSnapshot {
  primary: ModelProfile;
  profiles: ModelProfile[];
  activeModelId: string | null;
  current: Omit<ModelLaunchSpec, "apiKey"> | null;
  recentModels: string[];
  catalogRefreshedAt?: number;
  catalogTtlMs?: number;
}

export interface AddModelInput {
  name: string;
  adapter: "openai" | "openai-compatible";
  baseUrl: string;
  model: string;
  apiKey: string;
  vision?: boolean;
}

export interface EditModelInput extends AddModelInput {
  selection: string;
}

export interface ModelTestResult {
  ok: boolean;
  selection: string;
  status: number;
  latencyMs: number;
  endpoint: string;
  model: string;
  modelListed: boolean;
  discoveredModels: number;
  capabilities: {
    chat: boolean;
    streaming: boolean;
    vision: boolean;
    reasoning: boolean;
  };
}

interface BridgeEnvelope<T> {
  ok: boolean;
  result?: T;
  error?: string;
}

interface RegistrySnapshot {
  primary: ModelProfile;
  profiles: ModelProfile[];
  activeModelId: string | null;
}

interface ModelMetadataSnapshot {
  profiles: ModelProfile[];
}

export const PRIMARY_SELECTION = "builtin:minimax";
const ANT_LING_SELECTION = "builtin:ant-ling";
const ANT_LING_SELECTION_PREFIX = "builtin:ant-ling:";

function isAntLingSelection(selection: string): boolean {
  const value = String(selection || "").trim();
  return value === ANT_LING_SELECTION || value.startsWith(ANT_LING_SELECTION_PREFIX);
}

// TTL freshness must use a monotonic clock. Date.now() can jump backwards after
// NTP corrections, suspend/resume or a manual clock change and accidentally pin
// a cache entry as fresh. Keep Date.now() only for the user-facing refresh label.
function monotonicNow(): number {
  return typeof performance !== "undefined" && typeof performance.now === "function"
    ? performance.now()
    : Number(process.hrtime.bigint() / 1_000_000n);
}

interface LaunchCacheEntry {
  // Bridge-owned values (including credentials) are stable until the selection
  // is explicitly invalidated. Catalog-owned fields are projected into `spec`.
  base: ModelLaunchSpec;
  spec: ModelLaunchSpec;
  // Explicit user choices must survive a provider-catalog refresh.
  modelOverride?: string;
  reasoningOverride?: ModelReasoningState | null;
}

const EMPTY_REGISTRY: RegistrySnapshot = {
  primary: null as unknown as ModelProfile,
  profiles: [],
  activeModelId: null,
};

interface AntLingRegistrySnapshot {
  profiles: ModelProfile[];
  activeSelection: string | null;
}

export class DesktopModelManager {
  private currentSpec: ModelLaunchSpec | null = null;
  private recentModels: string[] = [];
  private registryCache: RegistrySnapshot | null = null;
  private registryCacheAt = 0;
  private registryCacheMonotonicAt = 0;
  private metadataCache: ModelMetadataSnapshot | null = null;
  private launchCache = new Map<string, LaunchCacheEntry>();
  private catalogRefreshPromise: Promise<ModelSnapshot> | null = null;
  private readonly catalogTtlMs: number;

  constructor(private readonly repoRoot: string) {
    const configured = Number(process.env.LOOM_MODEL_CATALOG_TTL_MS || "");
    this.catalogTtlMs = Number.isFinite(configured) && configured > 0
      ? Math.min(30 * 60_000, Math.max(30_000, Math.round(configured)))
      : 5 * 60_000;
  }

  get current(): ModelLaunchSpec | null {
    return this.currentSpec;
  }

  ensureInitial(): ModelLaunchSpec {
    if (this.currentSpec) return this.currentSpec;
    const registry = this.registry();
    const active = registry.profiles.find((profile) => profile.id === registry.activeModelId);
    const selection = active?.selection || PRIMARY_SELECTION;
    this.currentSpec = this.resolve(selection);
    return this.currentSpec;
  }

  private metadata(forceRefresh = false): ModelMetadataSnapshot {
    if (!forceRefresh && this.metadataCache) return this.metadataCache;
    this.metadataCache = this.runAdmin<ModelMetadataSnapshot>("metadata", {});
    return this.metadataCache;
  }

  private antLingRegistry(): AntLingRegistrySnapshot {
    return this.runPythonBridge<AntLingRegistrySnapshot>("loom_ant_ling_bridge.py", "list", {});
  }

  private antLingRegistryAsync(): Promise<AntLingRegistrySnapshot> {
    return this.runPythonBridgeAsync<AntLingRegistrySnapshot>("loom_ant_ling_bridge.py", "list", {});
  }

  private mergeAntLingRegistry(registry: RegistrySnapshot, antLing: AntLingRegistrySnapshot): RegistrySnapshot {
    const profiles = [
      ...registry.profiles.filter((profile) => profile.groupId !== "ant-ling"),
      ...antLing.profiles,
    ];
    const activeAntLing = antLing.activeSelection
      ? profiles.find((profile) => profile.selection === antLing.activeSelection)
      : undefined;
    return {
      ...registry,
      profiles,
      activeModelId: activeAntLing?.id ?? registry.activeModelId,
    };
  }

  private describeModel(selection: string, model: string): ModelProfile {
    return isAntLingSelection(selection)
      ? this.runPythonBridge<ModelProfile>("loom_ant_ling_bridge.py", "describe-model", { selection, model })
      : this.runBridge<ModelProfile>("describe-model", { selection, model });
  }

  private mergeRegistry(registry: RegistrySnapshot, metadata: ModelMetadataSnapshot): RegistrySnapshot {
    const declared = new Map(metadata.profiles.map((profile) => [profile.selection, profile]));
    const profiles = registry.profiles.map((profile) => {
      const safe = declared.get(profile.selection);
      return {
        ...profile,
        vision: safe?.vision ?? profile.vision ?? true,
        available: profile.available !== false,
      };
    });
    const primary = profiles.find((profile) => profile.selection === registry.primary.selection)
      ?? { ...registry.primary, vision: registry.primary.vision ?? true, available: true };
    return { ...registry, primary, profiles };
  }

  private preserveLastKnownProviderCatalog(next: RegistrySnapshot): RegistrySnapshot {
    const previous = this.registryCache;
    if (!previous) return next;

    const degradedGroups = new Set(
      next.profiles
        .filter((profile) => profile.catalogSource === "fallback")
        .map((profile) => profile.groupId || profile.selection),
    );
    const relayDegraded = next.profiles.some((profile) =>
      Boolean(
        profile.setupOnly
        && profile.statusMessage
        && (profile.groupId === "managed-relay" || profile.groupId?.startsWith("managed-relay:")),
      ),
    );
    if (!degradedGroups.size && !relayDegraded) return next;

    const retained = previous.profiles.filter((profile) => {
      if (profile.catalogSource !== "provider") return false;
      const groupId = profile.groupId || profile.selection;
      if (degradedGroups.has(groupId)) return true;
      return Boolean(relayDegraded && groupId.startsWith("managed-relay"));
    });
    if (!retained.length) return next;

    const retainedSelections = new Set(retained.map((profile) => profile.selection));
    const profiles = [
      ...retained,
      ...next.profiles.filter((profile) => !retainedSelections.has(profile.selection)),
    ];
    const primary = profiles.find((profile) => profile.selection === next.primary.selection)
      ?? next.primary;
    return { ...next, profiles, primary };
  }

  private adoptRegistry(registry: RegistrySnapshot, metadata: ModelMetadataSnapshot): RegistrySnapshot {
    const merged = this.preserveLastKnownProviderCatalog(this.mergeRegistry(registry, metadata));
    this.registryCache = merged;
    this.registryCacheAt = Date.now();
    this.registryCacheMonotonicAt = monotonicNow();

    const currentSelection = this.currentSpec?.selection ?? "";
    const liveSelections = new Set(merged.profiles.map((profile) => profile.selection));
    for (const [selection, entry] of [...this.launchCache.entries()]) {
      if (selection !== currentSelection && !liveSelections.has(selection)) {
        this.launchCache.delete(selection);
        continue;
      }
      const refreshed = this.refreshLaunchEntry(entry, merged, metadata);
      this.launchCache.set(selection, refreshed);
      // Keep the active runtime spec coherent too. The old patch refreshed only
      // launchCache, but main.ts reads `current` directly when starting/switching
      // the runtime, so leaving currentSpec stale could retain old limits/auth.
      if (selection === currentSelection) this.currentSpec = refreshed.spec;
    }
    return merged;
  }

  private refreshLaunchEntry(
    entry: LaunchCacheEntry,
    registry: RegistrySnapshot,
    metadata: ModelMetadataSnapshot,
  ): LaunchCacheEntry {
    const projected = this.projectLaunchSpec(entry.base, registry, metadata);
    return {
      ...entry,
      spec: {
        ...projected,
        ...(entry.modelOverride ? { model: entry.modelOverride } : {}),
        ...(entry.reasoningOverride !== undefined ? { reasoning: entry.reasoningOverride } : {}),
      },
    };
  }

  private projectLaunchSpec(
    base: ModelLaunchSpec,
    registry: RegistrySnapshot,
    metadata: ModelMetadataSnapshot,
  ): ModelLaunchSpec {
    const safe = metadata.profiles.find((profile) => profile.selection === base.selection);
    const catalog = registry.profiles.find((profile) => profile.selection === base.selection);
    return {
      ...base,
      groupId: catalog?.groupId ?? base.groupId,
      groupName: catalog?.groupName ?? base.groupName,
      groupOrder: catalog?.groupOrder ?? base.groupOrder,
      family: catalog?.family ?? base.family,
      protocol: catalog?.protocol ?? base.protocol,
      configured: catalog?.configured ?? base.configured,
      available: catalog?.available ?? base.available ?? true,
      catalogSource: catalog?.catalogSource ?? base.catalogSource,
      contextLimits: catalog?.contextLimits ?? base.contextLimits,
      reasoning: catalog?.reasoning ?? base.reasoning ?? null,
      vision: safe?.vision ?? catalog?.vision ?? base.vision ?? true,
      authMode: catalog?.authMode ?? base.authMode,
    };
  }

  private catalogFresh(): boolean {
    if (!this.registryCache || !this.registryCacheMonotonicAt) return false;
    return monotonicNow() - this.registryCacheMonotonicAt < this.catalogTtlMs;
  }

  registry(forceRefresh = false): RegistrySnapshot {
    // Enforce the same TTL on synchronous callers as listSnapshot(). Previously
    // this path returned any cached registry forever, making the TTL decorative.
    if (!forceRefresh && this.registryCache && this.catalogFresh()) return this.registryCache;
    const registry = this.mergeAntLingRegistry(
      this.runBridge<RegistrySnapshot>("list", {}),
      this.antLingRegistry(),
    );
    const metadata = this.metadata(forceRefresh);
    return this.adoptRegistry(registry, metadata);
  }

  private invalidateCaches(launchSelections: readonly string[] = []): void {
    this.registryCache = null;
    this.registryCacheAt = 0;
    this.registryCacheMonotonicAt = 0;
    this.metadataCache = null;
    for (const selection of launchSelections) this.launchCache.delete(selection);
  }

  private snapshotFromRegistry(spec: ModelLaunchSpec | null, registry: RegistrySnapshot): ModelSnapshot {
    const currentProfile = spec
      ? registry.profiles.find((profile) => profile.selection === spec.selection)
      : undefined;
    const current = spec
      ? {
          selection: spec.selection,
          id: spec.id,
          kind: spec.kind,
          name: spec.name,
          adapter: spec.adapter,
          baseUrl: spec.baseUrl,
          model: spec.model,
          provider: spec.provider,
          groupId: currentProfile?.groupId ?? spec.groupId,
          groupName: currentProfile?.groupName ?? spec.groupName,
          groupOrder: currentProfile?.groupOrder ?? spec.groupOrder,
          family: currentProfile?.family ?? spec.family,
          protocol: currentProfile?.protocol ?? spec.protocol,
          configured: currentProfile?.configured ?? spec.configured,
          available: currentProfile ? currentProfile.available !== false : false,
          catalogSource: currentProfile?.catalogSource ?? (spec ? "runtime" : undefined),
          statusMessage: currentProfile?.statusMessage ?? (
            spec && !currentProfile
              ? "This model is no longer advertised by the provider. The current conversation keeps its binding until you choose another model."
              : undefined
          ),
          vision: currentProfile?.vision ?? spec.vision ?? true,
          contextLimits: currentProfile?.contextLimits ?? spec.contextLimits,
          reasoning: currentProfile?.reasoning ?? spec.reasoning ?? null,
          authMode: currentProfile?.authMode ?? spec.authMode,
        }
      : null;

    let profiles = registry.profiles;
    if (spec && !currentProfile) {
      const unavailable: ModelProfile = {
        selection: spec.selection,
        id: spec.id,
        kind: spec.kind,
        name: spec.name,
        adapter: spec.adapter,
        baseUrl: spec.baseUrl,
        model: spec.model,
        groupId: spec.groupId,
        groupName: spec.groupName,
        groupOrder: spec.groupOrder,
        family: spec.family,
        protocol: spec.protocol,
        configured: spec.configured,
        available: false,
        catalogSource: "runtime",
        statusMessage: "No longer advertised by the provider",
        vision: spec.vision ?? true,
        contextLimits: spec.contextLimits,
        reasoning: spec.reasoning ?? null,
        authMode: spec.authMode,
      };
      profiles = [...profiles, unavailable];
    }

    return {
      ...registry,
      profiles,
      current,
      recentModels: [...this.recentModels],
      catalogRefreshedAt: this.registryCacheAt || undefined,
      catalogTtlMs: this.catalogTtlMs,
    };
  }

  snapshotFor(spec: ModelLaunchSpec | null, forceRefresh = false): ModelSnapshot {
    const registry = this.registry(forceRefresh);
    return this.snapshotFromRegistry(spec, registry);
  }

  snapshot(forceRefresh = false): ModelSnapshot {
    return this.snapshotFor(this.currentSpec, forceRefresh);
  }

  async listSnapshot(forceRefresh = false): Promise<ModelSnapshot> {
    if (!forceRefresh && this.catalogFresh() && this.registryCache) {
      return this.snapshotFromRegistry(this.currentSpec, this.registryCache);
    }
    if (this.catalogRefreshPromise) return this.catalogRefreshPromise;

    this.catalogRefreshPromise = (async () => {
      try {
        const [baseRegistry, metadata, antLing] = await Promise.all([
          this.runBridgeAsync<RegistrySnapshot>("list", {}),
          this.runAdminAsync<ModelMetadataSnapshot>("metadata", {}),
          this.antLingRegistryAsync(),
        ]);
        const registry = this.mergeAntLingRegistry(baseRegistry, antLing);
        const adopted = this.adoptRegistry(registry, metadata);
        this.metadataCache = metadata;
        return this.snapshotFromRegistry(this.currentSpec, adopted);
      } catch (error) {
        if (this.registryCache) {
          return this.snapshotFromRegistry(this.currentSpec, this.registryCache);
        }
        throw error;
      } finally {
        this.catalogRefreshPromise = null;
      }
    })();
    return this.catalogRefreshPromise;
  }

  resolve(selection: string): ModelLaunchSpec {
    const cached = this.launchCache.get(selection);
    if (cached) return cached.spec;
    const base = isAntLingSelection(selection)
      ? this.runPythonBridge<ModelLaunchSpec>("loom_ant_ling_bridge.py", "resolve", { selection })
      : this.runBridge<ModelLaunchSpec>("resolve", { selection });
    const spec = this.projectLaunchSpec(base, this.registryCache ?? EMPTY_REGISTRY, this.metadata());
    this.launchCache.set(selection, { base, spec });
    return spec;
  }

  add(input: AddModelInput): ModelProfile {
    const profile = this.runBridge<ModelProfile>("save", input as unknown as Record<string, unknown>);
    this.invalidateCaches([profile.selection]);
    return profile;
  }

  update(input: EditModelInput): ModelProfile {
    const selection = String(input.selection || "").trim();
    if (!selection) throw new Error("Model profile is required");
    const profile = this.runAdmin<ModelProfile>("update", input as unknown as Record<string, unknown>);
    this.invalidateCaches([selection]);
    if (this.currentSpec?.selection === selection) {
      this.currentSpec = this.resolve(selection);
    }
    return profile;
  }

  test(selection: string): ModelTestResult {
    const value = String(selection || "").trim();
    if (!value) throw new Error("Model profile is required");
    return this.runAdmin<ModelTestResult>("test", { selection: value });
  }

  delete(selection: string): RegistrySnapshot {
    const value = String(selection || "").trim();
    if (!value) throw new Error("Model profile is required");
    const registry = this.runBridge<RegistrySnapshot>("delete", { selection: value });
    this.invalidateCaches([value]);
    if (this.currentSpec?.selection === value) this.currentSpec = null;
    return registry;
  }

  markActive(selection: string): void {
    if (!this.registryCache) return;
    const active = this.registryCache.profiles.find((profile) => profile.selection === selection);
    this.registryCache = { ...this.registryCache, activeModelId: active?.id ?? null };
  }

  persistActive(selection: string): void {
    if (isAntLingSelection(selection)) {
      this.runPythonBridge<{ selection: string }>("loom_ant_ling_bridge.py", "set-active", { selection });
      return;
    }
    this.runBridge<{ selection: string }>("persist-active", { selection });
  }

  setActive(selection: string): void {
    this.markActive(selection);
    this.persistActive(selection);
  }

  setProviderKey(provider: string, apiKey: string): ModelSnapshot {
    const value = String(provider || "").trim();
    const secret = String(apiKey || "").trim();
    if (!value) throw new Error("Provider is required");
    if (!secret) throw new Error("API key is required");
    if (value.toLowerCase() === "ant-ling") {
      throw new Error("Ant Ling built-in models use your Loom account. Use Add connection for your own Ant Ling API key.");
    }
    this.runBridge<{ provider: string; configured: boolean }>("set-provider-key", { provider: value, apiKey: secret });
    // A resolved launch base embeds credentials. Any selection may therefore be
    // stale after a key rotation; a hardcoded provider-prefix list is unsafe.
    const currentSelection = this.currentSpec?.selection ?? null;
    this.invalidateCaches([...this.launchCache.keys()]);
    if (currentSelection) this.currentSpec = this.resolve(currentSelection);
    return this.snapshot(true);
  }

  setReasoning(kind: string, value: string): ModelReasoningState {
    const current = this.currentSpec ?? this.ensureInitial();
    const profile = isAntLingSelection(current.selection)
      ? this.runPythonBridge<ModelProfile>("loom_ant_ling_bridge.py", "set-reasoning", {
          selection: current.selection,
          model: current.model,
          kind,
          value,
        })
      : this.runBridge<ModelProfile>("set-reasoning", {
          selection: current.selection,
          model: current.model,
          kind,
          value,
        });
    if (!profile.reasoning) throw new Error("Selected model does not expose reasoning controls");
    this.currentSpec = { ...current, reasoning: profile.reasoning };
    const entry = this.launchCache.get(current.selection);
    this.launchCache.set(current.selection, {
      base: entry?.base ?? this.currentSpec,
      spec: this.currentSpec,
      ...(entry?.modelOverride ? { modelOverride: entry.modelOverride } : {}),
      reasoningOverride: profile.reasoning,
    });
    if (this.registryCache) {
      this.registryCache = {
        ...this.registryCache,
        profiles: this.registryCache.profiles.map((item) =>
          item.selection === current.selection && item.model === current.model
            ? { ...item, reasoning: profile.reasoning }
            : item
        ),
      };
    }
    return profile.reasoning;
  }

  useProfile(selection: string): ModelLaunchSpec {
    const next = this.resolve(selection);
    this.rememberCurrentModel(next.model);
    this.currentSpec = next;
    return next;
  }

  resolveModelNameFor(selection: string, model: string): ModelLaunchSpec {
    const value = String(model || "").trim();
    if (!value) throw new Error("Model ID must not be empty");
    const current = this.resolve(selection);
    const described = this.describeModel(selection, value);
    const connection = described.selection && described.selection !== current.selection
      ? this.resolve(described.selection)
      : current;
    return {
      ...connection,
      model: described.model || value,
      reasoning: described.reasoning ?? null,
    };
  }

  useModelName(model: string): ModelLaunchSpec {
    const value = String(model || "").trim();
    if (!value) throw new Error("Model ID must not be empty");
    const current = this.currentSpec ?? this.ensureInitial();
    if (value !== current.model) this.rememberCurrentModel(current.model);
    const described = this.describeModel(current.selection, value);
    const connection = described.selection && described.selection !== current.selection
      ? this.resolve(described.selection)
      : current;
    const next: ModelLaunchSpec = {
      ...connection,
      model: described.model || value,
      reasoning: described.reasoning ?? null,
    };
    this.currentSpec = next;
    const entry = this.launchCache.get(next.selection);
    this.launchCache.set(next.selection, {
      base: entry?.base ?? next,
      spec: next,
      modelOverride: described.model || value,
      reasoningOverride: described.reasoning ?? null,
    });
    return next;
  }

  restore(spec: ModelLaunchSpec): void {
    this.currentSpec = spec;
  }

  private rememberCurrentModel(model: string): void {
    const value = String(model || "").trim();
    if (!value) return;
    this.recentModels = [value, ...this.recentModels.filter((entry) => entry !== value)].slice(0, 8);
  }

  private runBridge<T>(
    command: "list" | "resolve" | "describe-model" | "save" | "delete" | "set-active" | "persist-active" | "set-reasoning" | "set-provider-key",
    payload: Record<string, unknown>,
  ): T {
    return this.runPythonBridge<T>("loom_model_bridge.py", command, payload);
  }

  private runBridgeAsync<T>(
    command: "list",
    payload: Record<string, unknown>,
  ): Promise<T> {
    return this.runPythonBridgeAsync<T>("loom_model_bridge.py", command, payload);
  }

  private runAdmin<T>(command: "metadata" | "update" | "test", payload: Record<string, unknown>): T {
    return this.runPythonBridge<T>("loom_model_admin.py", command, payload);
  }

  private runAdminAsync<T>(command: "metadata", payload: Record<string, unknown>): Promise<T> {
    return this.runPythonBridgeAsync<T>("loom_model_admin.py", command, payload);
  }

  private runPythonBridgeAsync<T>(
    scriptName: string,
    command: string,
    payload: Record<string, unknown>,
  ): Promise<T> {
    const python = process.env.LOOM_PYTHON || (process.platform === "win32" ? "python" : "python3");
    const script = path.join(this.repoRoot, scriptName);
    return new Promise<T>((resolve, reject) => {
      const child = spawn(python, [script, command], {
        cwd: this.repoRoot,
        env: { ...process.env, PYTHONUTF8: "1" },
        windowsHide: true,
        stdio: ["pipe", "pipe", "pipe"],
        timeout: 20_000,
      });
      let stdout = "";
      let stderr = "";
      let settled = false;
      child.stdout.setEncoding("utf8");
      child.stderr.setEncoding("utf8");
      child.stdout.on("data", (chunk) => { stdout += String(chunk); });
      child.stderr.on("data", (chunk) => { stderr += String(chunk); });
      child.on("error", (error) => {
        if (settled) return;
        settled = true;
        reject(error);
      });
      child.on("close", (code) => {
        if (settled) return;
        settled = true;
        const raw = stdout.trim();
        if (!raw) {
          reject(new Error(stderr.trim() || `${scriptName} exited with ${code ?? "unknown status"}`));
          return;
        }
        try {
          const envelope = JSON.parse(raw) as BridgeEnvelope<T>;
          if (!envelope.ok || envelope.result === undefined) {
            reject(new Error(envelope.error || `${scriptName} request failed`));
            return;
          }
          resolve(envelope.result);
        } catch {
          reject(new Error(`${scriptName} returned invalid JSON: ${raw.slice(0, 240)}`));
        }
      });
      child.stdin.end(JSON.stringify(payload));
    });
  }

  private runPythonBridge<T>(scriptName: string, command: string, payload: Record<string, unknown>): T {
    const python = process.env.LOOM_PYTHON || (process.platform === "win32" ? "python" : "python3");
    const script = path.join(this.repoRoot, scriptName);
    const result = spawnSync(python, [script, command], {
      cwd: this.repoRoot,
      env: { ...process.env, PYTHONUTF8: "1" },
      input: JSON.stringify(payload),
      encoding: "utf8",
      windowsHide: true,
      maxBuffer: 1024 * 1024,
    });

    if (result.error) throw result.error;
    const stdout = String(result.stdout || "").trim();
    if (!stdout) {
      const detail = String(result.stderr || "").trim();
      throw new Error(detail || `${scriptName} exited with ${result.status ?? "unknown status"}`);
    }

    let envelope: BridgeEnvelope<T>;
    try {
      envelope = JSON.parse(stdout) as BridgeEnvelope<T>;
    } catch {
      throw new Error(`${scriptName} returned invalid JSON: ${stdout.slice(0, 240)}`);
    }
    if (!envelope.ok || envelope.result === undefined) {
      throw new Error(envelope.error || `${scriptName} request failed`);
    }
    return envelope.result;
  }
}
