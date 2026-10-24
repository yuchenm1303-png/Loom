import type { CSSProperties } from "react";
import { Cpu } from "./icons";
import { AnthropicGlyph, GeminiGlyph } from "./ToolIconLibrary";
import { resolveModelBrand } from "./modelBrand";
import openai from "../assets/provider-logos/openai.svg";
import deepseek from "../assets/provider-logos/deepseek.svg";
import minimax from "../assets/provider-logos/minimax.svg";
import opencode from "../assets/provider-logos/opencode.svg";
import qwen from "../assets/provider-logos/qwen.svg";
import moonshot from "../assets/provider-logos/moonshotai.svg";
import xiaomi from "../assets/provider-logos/xiaomi.svg";
import hunyuan from "../assets/provider-logos/tencenthy.svg";
import xai from "../assets/provider-logos/x.svg";
import glm from "../assets/provider-logos/glm.svg";
import zhipu from "../assets/provider-logos/zhipu.svg";
import mistral from "../assets/provider-logos/mistral.svg";
import meta from "../assets/provider-logos/meta.svg";
import "./model-brand-icon.css";

const logos: Record<string, string> = { openai, deepseek, minimax, opencode, qwen, moonshot, xiaomi, hunyuan, xai, glm, zhipu, mistral, meta };

export function ModelBrandIcon({ identities, size = 20 }: { identities: (string | undefined)[]; size?: number }) {
  const brand = resolveModelBrand(...identities);
  const logo = brand ? logos[brand] : undefined;
  const style = { width: size, height: size, "--model-brand-logo": logo ? `url("${logo}")` : undefined } as CSSProperties;
  return <span className={`model-brand-icon${logo ? " has-logo" : ""}`} data-model-brand={brand || "custom"} style={style} aria-hidden="true">
    {brand === "anthropic" ? <AnthropicGlyph size={size} /> : brand === "gemini" ? <GeminiGlyph size={size} /> : !logo ? <Cpu size={size} /> : null}
  </span>;
}
