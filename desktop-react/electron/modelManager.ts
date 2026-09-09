import { spawnSync } from "node:child_process";
import path from "node:path";

export interface ModelProfile {
  selection: string;
  id: string;
  kind: "builtin" | "saved";
  name: string;
  adapter: "openai" | "openai-compatible" | string;
  baseUrl: string;
  model: string;
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

export const PRIMARY_SELECTION = "builtin:minimax";

export class DesktopModelManager {
  private currentSpec: ModelLaunchSpec | null = null;
  private recentModels: string[] = [];

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

  registry(): RegistrySnapshot {
    return this.runBridge<RegistrySnapshot>("list", {});
  }

  snapshot(): ModelSnapshot {
    const registry = this.registry();
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
    return resolved;
  }

  add(input: AddModelInput): ModelProfile {
    // ``AddModelInput`` is a concrete record that the registry's runtime
    // serializer rejects as ``Record<string, unknown>`` because its properties
    // are not implicitly indexable. The bridge command itself serializes the
    // payload, so widening to ``unknown`` at this seam is the right place to
    // bridge a typed input into an indexable record.
    return this.runBridge<ModelProfile>("save", input as unknown as Record<string, unknown>);
  }

  setActive(selection: string): void {
    this.runBridge<RegistrySnapshot>("set-active", { selection });
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
    const next: ModelLaunchSpec = { ...current, model: value };
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

  private runBridge<T>(command: "list" | "resolve" | "save" | "set-active", payload: Record<string, unknown>): T {
    const python = process.env.LOOM_PYTHON || (process.platform === "win32" ? "python" : "python3");
    const script = path.join(this.repoRoot, "loom_model_bridge.py");
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
      throw new Error(detail || `Model bridge exited with ${result.status ?? "unknown status"}`);
    }

    let envelope: BridgeEnvelope<T>;
    try {
      envelope = JSON.parse(stdout) as BridgeEnvelope<T>;
    } catch {
      throw new Error(`Model bridge returned invalid JSON: ${stdout.slice(0, 240)}`);
    }
    if (!envelope.ok || envelope.result === undefined) {
      throw new Error(envelope.error || "Model bridge request failed");
    }
    return envelope.result;
  }
}
