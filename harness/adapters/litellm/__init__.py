"""litellm implementation of the LLMProvider Port (ADR-0013, ADR-0015, ADR-0016)."""

from harness.adapters.litellm.provider import CompletionCallable, LiteLLMProvider

__all__ = ["CompletionCallable", "LiteLLMProvider"]
