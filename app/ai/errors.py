from __future__ import annotations


class AIPlatformError(RuntimeError):
    """Base error for the detached provider-neutral AI platform."""


class AIConfigurationError(AIPlatformError, ValueError):
    pass


class AICredentialError(AIPlatformError):
    pass


class AITransportError(AIPlatformError):
    """Provider/network failure with an explicit retry contract.

    Callers used to treat every transport-shaped exception as transient.  That
    turns permanent provider rejections (for example HTTP 402 or 401) into
    duplicate full-context requests.  Keep the default retryable for legacy
    adapters, while allowing adapters that know the status to fail closed.
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = bool(retryable)


class AIResponseError(AIPlatformError):
    pass


class AIEmptyResponseError(AIResponseError):
    """Provider completed a response without public text or a tool call.

    This is kept separate from malformed non-empty responses because compatible
    providers can transiently emit an empty or reasoning-only completion.  The
    agent runtime may safely retry it before anything is committed or executed.
    """

    def __init__(
        self,
        message: str,
        *,
        finish_reason: str = "",
        response_id: str = "",
        reasoning_char_count: int = 0,
        chunk_count: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
    ) -> None:
        super().__init__(message)
        self.finish_reason = str(finish_reason or "")
        self.response_id = str(response_id or "")
        self.reasoning_char_count = max(0, int(reasoning_char_count))
        self.chunk_count = max(0, int(chunk_count))
        self.input_tokens = max(0, int(input_tokens))
        self.output_tokens = max(0, int(output_tokens))
        self.total_tokens = max(0, int(total_tokens))


__all__ = [
    "AIConfigurationError",
    "AICredentialError",
    "AIEmptyResponseError",
    "AIPlatformError",
    "AIResponseError",
    "AITransportError",
]
