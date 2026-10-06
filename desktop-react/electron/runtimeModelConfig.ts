import type { ModelLaunchSpec } from "./modelManager.js";

/** Cold startup uses the same profile metadata as runtime/set_model. */
export function runtimeModelArguments(spec: ModelLaunchSpec): string[] {
  return ["--context-limits", JSON.stringify(spec.contextLimits ?? {}),
    spec.vision === false ? "--no-vision" : "--vision"];
}

export function runtimeModelParams(spec: ModelLaunchSpec): Record<string, unknown> {
  return {
    selection: spec.selection, provider: spec.provider, baseUrl: spec.baseUrl,
    model: spec.model, apiKey: spec.apiKey, vision: spec.vision !== false,
    contextLimits: spec.contextLimits,
    reasoningKind: spec.reasoning?.kind ?? "",
    reasoningValue: spec.reasoning?.value ?? "",
  };
}
