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
  reasoning?: ModelReasoningState | null;
  managed?: boolean;
  available?: boolean;
  availabilityReason?: string;
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
  managedCatalogError?: string;
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
  managedCatalogError?: string;
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
          reasoning: this.currentSpec.reasoning ?? null,
          managed: this.currentSpec.managed,
          available: this.currentSpec.available,
          availabilityReason: this.currentSpec.availabilityReason,
        }
      : null;
    return {
      ...registry,
      current,
      recentModels: [...this.recentModels],
    };
  }

  resolve(selection: string): ModelLaunchSpec {
    return this.runBridge<ModelLaunchSpec>("resolve", { selection });
  }

  add(input: AddModelInput): ModelProfile {
    return this.runBridge<ModelProfile>("save", input as unknown as Record<string, unknown>);
  }

  setActive(selection: string): void {
    this.runBridge<RegistrySnapshot>("set-active", { selection });
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
    if (current.managed) {
      throw new Error("Managed models must be selected from the server-controlled model list.");
    }
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

  provisionManagedRelay(credential: string): void {
    const value = String(credential || "").trim();
    if (!value) throw new Error("Managed relay credential must not be empty");
    this.runBridge<{ provisioned: boolean }>("provision-managed-relay", { credential: value });
  }

  private rememberCurrentModel(model: string): void {
    const value = String(model || "").trim();
    if (!value) return;
    this.recentModels = [value, ...this.recentModels.filter((entry) => entry !== value)].slice(0, 8);
  }

  private runBridge<T>(
    command: "list" | "resolve" | "describe-model" | "save" | "set-active" | "set-reasoning" | "provision-managed-relay",
    payload: Record<string, unknown>,
  ): T {
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
