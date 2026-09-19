import { spawnSync } from "node:child_process";
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
  vision?: boolean;
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
  private metadataCache: ModelMetadataSnapshot | null = null;

  constructor(private readonly repoRoot: string) {}

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

  registry(forceRefresh = false): RegistrySnapshot {
    if (!forceRefresh && this.registryCache) return this.registryCache;
    const registry = this.runBridge<RegistrySnapshot>("list", {});
    const metadata = this.metadata(forceRefresh);
    const declared = new Map(metadata.profiles.map((profile) => [profile.selection, profile]));
    const profiles = registry.profiles.map((profile) => {
      const safe = declared.get(profile.selection);
      return { ...profile, vision: safe?.vision ?? profile.vision ?? true };
    });
    const primary = profiles.find((profile) => profile.selection === registry.primary.selection)
      ?? { ...registry.primary, vision: registry.primary.vision ?? true };
    this.registryCache = { ...registry, primary, profiles };
    return this.registryCache;
  }

  snapshot(forceRefresh = false): ModelSnapshot {
    const registry = this.registry(forceRefresh);
    const currentProfile = this.currentSpec
      ? registry.profiles.find((profile) => profile.selection === this.currentSpec?.selection)
      : undefined;
    const current = this.currentSpec
      ? {
          selection: this.currentSpec.selection,
          id: this.currentSpec.id,
          kind: this.currentSpec.kind,
          name: this.currentSpec.name,
          adapter: this.currentSpec.adapter,
          baseUrl: this.currentSpec.baseUrl,
          model: this.currentSpec.model,
          provider: this.currentSpec.provider,
          vision: currentProfile?.vision ?? this.currentSpec.vision ?? true,
          reasoning: this.currentSpec.reasoning ?? null,
        }
      : null;
    return {
      ...registry,
      current,
      recentModels: [...this.recentModels],
    };
  }

  resolve(selection: string): ModelLaunchSpec {
    const resolved = this.runBridge<ModelLaunchSpec>("resolve", { selection });
    const safe = this.metadata().profiles.find((profile) => profile.selection === selection);
    return { ...resolved, vision: safe?.vision ?? resolved.vision ?? true };
  }

  add(input: AddModelInput): ModelProfile {
    const profile = this.runBridge<ModelProfile>("save", input as unknown as Record<string, unknown>);
    this.registryCache = null;
    this.metadataCache = null;
    return profile;
  }

  update(input: EditModelInput): ModelProfile {
    const selection = String(input.selection || "").trim();
    if (!selection) throw new Error("Model profile is required");
    const profile = this.runAdmin<ModelProfile>("update", input as unknown as Record<string, unknown>);
    this.registryCache = null;
    this.metadataCache = null;
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
    this.registryCache = registry;
    this.metadataCache = null;
    if (this.currentSpec?.selection === value) this.currentSpec = null;
    return registry;
  }

  setActive(selection: string): void {
    this.runBridge<{ selection: string }>("persist-active", { selection });
    if (this.registryCache) {
      const active = this.registryCache.profiles.find((profile) => profile.selection === selection);
      this.registryCache = { ...this.registryCache, activeModelId: active?.id ?? null };
    }
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

  useModelName(model: string): ModelLaunchSpec {
    const value = String(model || "").trim();
    if (!value) throw new Error("Model ID must not be empty");
    const current = this.currentSpec ?? this.ensureInitial();
    if (value !== current.model) this.rememberCurrentModel(current.model);
    const described = this.runBridge<ModelProfile>("describe-model", {
      selection: current.selection,
      model: value,
    });
    const next: ModelLaunchSpec = {
      ...current,
      model: value,
      reasoning: described.reasoning ?? null,
    };
    this.currentSpec = next;
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
    command: "list" | "resolve" | "describe-model" | "save" | "delete" | "set-active" | "persist-active" | "set-reasoning",
    payload: Record<string, unknown>,
  ): T {
    return this.runPythonBridge<T>("loom_model_bridge.py", command, payload);
  }

  private runAdmin<T>(command: "metadata" | "update" | "test", payload: Record<string, unknown>): T {
    return this.runPythonBridge<T>("loom_model_admin.py", command, payload);
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
