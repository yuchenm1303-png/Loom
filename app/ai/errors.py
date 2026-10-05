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

    def __init__(self, message: str, *, retryable: bool = True, status_code: int | None = None,
                 retry_after_seconds: float = 0.0) -> None:
        super().__init__(message)
        self.retryable = bool(retryable)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class AIResponseError(AIPlatformError):
    def __init__(self, message: str, *, finish_reason: str = "") -> None:
        super().__init__(message)
        self.finish_reason = finish_reason


class AIQuotaExceeded(AITransportError):
    """A provider's exhausted allowance cannot recover through backoff."""

    def __init__(self, *, status_code: int | None = None) -> None:
        super().__init__("模型服务额度已耗尽，请补充额度或切换可用模型后继续。",
                         retryable=False, status_code=status_code)


class AITruncatedToolCallError(AIResponseError):
    """An explicit provider output limit prevented an atomic tool request."""

    def __init__(self, *, finish_reason: str, tool_name: str, argument_chars: int) -> None:
        super().__init__(f"tool call {tool_name!r} was truncated by the provider output limit")
        self.finish_reason = finish_reason
        self.tool_name = tool_name
        self.argument_chars = argument_chars


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
    "AITruncatedToolCallError",
]
