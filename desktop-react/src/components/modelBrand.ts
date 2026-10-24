export type ModelBrand = "openai" | "anthropic" | "gemini" | "deepseek" | "minimax" | "opencode" | "qwen" | "moonshot" | "xiaomi" | "hunyuan" | "xai" | "glm" | "zhipu" | "mistral" | "meta";

// Inspect model identity before the connection: a relay may serve many brands.
// The compatible API protocol itself does not identify a model's manufacturer.
export function resolveModelBrand(...identities: (string | undefined)[]): ModelBrand | null {
  for (const identity of identities) {
    const value = (identity || "").toLowerCase();
    if (/(^|[^a-z])(deepseek)([^a-z]|$)/.test(value)) return "deepseek";
    if (/(^|[^a-z])(claude|anthropic)([^a-z]|$)/.test(value)) return "anthropic";
    if (/(^|[^a-z])(gemini|google)([^a-z]|$)/.test(value)) return "gemini";
    if (/(^|[^a-z])(gpt|chatgpt|codex|o[134])([^a-z]|$)/.test(value) || value === "openai" || /api\.openai\.com/.test(value) || value === "managed-relay:openai") return "openai";
    if (/(^|[^a-z])(minimax)([^a-z]|$)/.test(value)) return "minimax";
    if (/(^|[^a-z])(qwen|qwq)([^a-z]|$)/.test(value)) return "qwen";
    if (/(^|[^a-z])(kimi|moonshot|moonshotai)([^a-z]|$)/.test(value)) return "moonshot";
    if (/(^|[^a-z])(mimo|xiaomi)([^a-z]|$)/.test(value)) return "xiaomi";
    if (/(^|[^a-z])(hunyuan|tencenthy)([^a-z]|$)/.test(value)) return "hunyuan";
    if (/(^|[^a-z])(grok|xai|x\.ai)([^a-z]|$)/.test(value)) return "xai";
    if (/(^|[^a-z])(glm)([^a-z]|$)/.test(value)) return "glm";
    if (/(^|[^a-z])(zhipu|zai|z\.ai)([^a-z]|$)/.test(value)) return "zhipu";
    if (/(^|[^a-z])(mistral|mixtral|codestral|devstral|ministral)([^a-z]|$)/.test(value)) return "mistral";
    if (/(^|[^a-z])(meta|llama)([^a-z]|$)/.test(value)) return "meta";
    if (/(^|[^a-z])(opencode)([^a-z]|$)/.test(value)) return "opencode";
  }
  return null;
}
