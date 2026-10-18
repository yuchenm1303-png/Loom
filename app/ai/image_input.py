"""Narrow, provider-neutral recognition of explicit image capability rejections."""
import re

from .errors import AITransportError

IMAGE_INPUT_UNSUPPORTED = "MODEL_IMAGE_INPUT_UNSUPPORTED: This model does not support image input. Switch to an image-capable model or remove the new image and retry."


class AIImageInputUnsupported(AITransportError):
    def __init__(self):
        super().__init__(IMAGE_INPUT_UNSUPPORTED, retryable=False, status_code=400)


def is_image_input_rejection(error: BaseException) -> bool:
    # A broken image URL/format, auth failure or transient outage is not evidence
    # that the model is text-only. Never infer capabilities from its name.
    if getattr(error, "status_code", None) not in (400, 422):
        return False
    text = str(error).lower()
    return bool(re.search(
        r"supports? (?:text input|text) only|(?:does not|doesn't|cannot|can't) support (?:image|vision)"
        r"|image input (?:is |are )?not supported|(?:image|vision) input (?:is |are )?unsupported",
        text,
    ))
