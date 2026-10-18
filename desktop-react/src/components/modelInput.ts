/** Capability notices are user choices, not application failures. */
export function imageInputError(message: string, zh: boolean): string | null {
  if (!/MODEL_IMAGE_INPUT_UNSUPPORTED|supports? (?:text input|text) only|(?:does not|doesn't|cannot|can't) support (?:image|vision)|image input (?:is |are )?not supported/i.test(message)) return null;
  return zh ? "该模型不支持图片输入。请切换到支持图片的模型，或移除本次新附图片后重试。原始对话中的图片会保留。"
    : "This model cannot read images. Switch to an image-capable model or remove the new image and retry. Images in the original conversation are preserved.";
}

export function imageAttachmentState(hasImages: boolean, allowed: boolean, support: boolean | null | undefined, zh: boolean) {
  return { blocked: hasImages && !allowed, message: !hasImages ? null : !allowed
    ? zh ? "当前模型或会话不支持图片输入。请切换模型，或移除图片后发送；其他附件仍可使用。"
      : "Image input is unavailable for this model or conversation. Switch models or remove the image before sending; other files still work."
    : support === null ? zh ? "尚未确认当前模型是否支持图片；发送后模型可能拒绝。"
      : "Image support for this model has not been confirmed; the provider may reject this image." : null };
}
