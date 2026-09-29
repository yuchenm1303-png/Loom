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

export class DesktopModelManager {
  private currentSpec: ModelLaunchSpec | null = null;
  private recentModels: string[] = [];
  private registryCache: RegistrySnapshot | null = null;
  private registryCacheAt = 0;
  private metadataCache: ModelMetadataSnapshot | null = null;
  private launchCache = new Map<string, ModelLaunchSpec>();
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

  private adoptRegistry(registry: RegistrySnapshot, metadata: ModelMetadataSnapshot): RegistrySnapshot {
    const merged = this.mergeRegistry(registry, metadata);
    this.registryCache = merged;
    this.registryCacheAt = Date.now();

    // A provider may remove/rename a model between refreshes. Drop stale launch
    // cache entries so a future click cannot resurrect an old provider listing.
    // The model already bound to the live conversation is deliberately kept:
    // it remains usable until the user changes it, and snapshotFor marks it as
    // unavailable instead of silently switching the conversation.
    const currentSelection = this.currentSpec?.selection ?? "";
    const liveSelections = new Set(merged.profiles.map((profile) => profile.selection));
    for (const selection of [...this.launchCache.keys()]) {
      if (selection !== currentSelection && !liveSelections.has(selection)) {
        this.launchCache.delete(selection);
      }
    }
    return merged;
  }

  private catalogFresh(): boolean {
    return Boolean(
      this.registryCache
      && this.registryCacheAt
      && Date.now() - this.registryCacheAt < this.catalogTtlMs
    );
  }

  registry(forceRefresh = false): RegistrySnapshot {
    if (!forceRefresh && this.registryCache) return this.registryCache;
    const registry = this.runBridge<RegistrySnapshot>("list", {});
    const metadata = this.metadata(forceRefresh);
    return this.adoptRegistry(registry, metadata);
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
        }
      : null;

    let profiles = registry.profiles;
    if (spec && !currentProfile) {
      // Preserve the missing current model as a disabled row. New catalog
      // entries appear immediately, removed entries disappear, but the active
      // thread never jumps to a different model behind the user's back.
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
        const [registry, metadata] = await Promise.all([
          this.runBridgeAsync<RegistrySnapshot>("list", {}),
          this.runAdminAsync<ModelMetadataSnapshot>("metadata", {}),
        ]);
        const adopted = this.adoptRegistry(registry, metadata);
        this.metadataCache = metadata;
        return this.snapshotFromRegistry(this.currentSpec, adopted);
      } catch (error) {
        // A transient /models outage must not make every model disappear.
        // Keep the last known good catalog when one exists; first launch still
        // surfaces the real discovery error so setup problems are diagnosable.
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
    if (cached) return cached;
    const resolved = this.runBridge<ModelLaunchSpec>("resolve", { selection });
    const safe = this.metadata().profiles.find((profile) => profile.selection === selection);
    const catalog = this.registryCache?.profiles.find((profile) => profile.selection === selection);
    // Provider discovery happens in the short-lived catalog bridge process.
    // Carry its published metadata into the durable launch spec here so a
    // newly discovered model keeps its real context/capability information
    // when the later resolve call runs in a fresh Python process.
    const next = {
      ...resolved,
      groupId: catalog?.groupId ?? resolved.groupId,
      groupName: catalog?.groupName ?? resolved.groupName,
      groupOrder: catalog?.groupOrder ?? resolved.groupOrder,
      family: catalog?.family ?? resolved.family,
      protocol: catalog?.protocol ?? resolved.protocol,
      configured: catalog?.configured ?? resolved.configured,
      available: catalog?.available ?? resolved.available ?? true,
      catalogSource: catalog?.catalogSource ?? resolved.catalogSource,
      contextLimits: catalog?.contextLimits ?? resolved.contextLimits,
      reasoning: catalog?.reasoning ?? resolved.reasoning ?? null,
      vision: safe?.vision ?? catalog?.vision ?? resolved.vision ?? true,
    };
    this.launchCache.set(selection, next);
    return next;
  }

  add(input: AddModelInput): ModelProfile {
    const profile = this.runBridge<ModelProfile>("save", input as unknown as Record<string, unknown>);
    this.registryCache = null;
    this.registryCacheAt = 0;
    this.metadataCache = null;
    this.launchCache.delete(profile.selection);
    return profile;
  }

  update(input: EditModelInput): ModelProfile {
    const selection = String(input.selection || "").trim();
    if (!selection) throw new Error("Model profile is required");
    const profile = this.runAdmin<ModelProfile>("update", input as unknown as Record<string, unknown>);
    this.registryCache = null;
    this.registryCacheAt = 0;
    this.metadataCache = null;
    this.launchCache.delete(selection);
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
    this.registryCache = null;
    this.registryCacheAt = 0;
    this.metadataCache = null;
    this.launchCache.delete(value);
    if (this.currentSpec?.selection === value) this.currentSpec = null;
    return registry;
  }

  markActive(selection: string): void {
    if (!this.registryCache) return;
    const active = this.registryCache.profiles.find((profile) => profile.selection === selection);
    this.registryCache = { ...this.registryCache, activeModelId: active?.id ?? null };
  }

  persistActive(selection: string): void {
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
    this.runBridge<{ provider: string; configured: boolean }>("set-provider-key", {
      provider: value,
      apiKey: secret,
    });
    this.registryCache = null;
    this.registryCacheAt = 0;
    this.metadataCache = null;
    for (const key of [...this.launchCache.keys()]) {
      if (
        key.startsWith("builtin:opencode-go:")
        || key.startsWith("managed:")
        || key === "builtin:cqu"
      ) {
        this.launchCache.delete(key);
      }
    }
    return this.snapshot(true);
  }

  setReasoning(kind: string, value: string): ModelReasoningState {
    const current = this.currentSpec ?? this.ensureInitial();
    const profile = this.runBridge<ModelProfile>("set-reasoning", {
      selection: current.selection,
      model: current.model,
      kind,
      value,
    });
    if (!profile.reasoning) throw new Error("Selected model does not expose reasoning controls");
    this.currentSpec = { ...current, reasoning: profile.reasoning };
    this.launchCache.set(current.selection, this.currentSpec);
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
    this.launchCache.set(selection, next);
    return next;
  }

  resolveModelNameFor(selection: string, model: string): ModelLaunchSpec {
    const value = String(model || "").trim();
    if (!value) throw new Error("Model ID must not be empty");
    const current = this.resolve(selection);
    const described = this.runBridge<ModelProfile>("describe-model", {
      selection,
      model: value,
    });
    // describe-model is allowed to canonicalize a known built-in model onto
    // its owning provider. Never keep the old connection while only changing
    // the visible model name: that can make the UI say DeepSeek while requests
    // still use MiniMax credentials/base URL.
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
    const described = this.runBridge<ModelProfile>("describe-model", {
      selection: current.selection,
      model: value,
    });
    const connection = described.selection && described.selection !== current.selection
      ? this.resolve(described.selection)
      : current;
    const next: ModelLaunchSpec = {
      ...connection,
      model: described.model || value,
      reasoning: described.reasoning ?? null,
    };
    this.currentSpec = next;
    this.launchCache.set(next.selection, next);
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
