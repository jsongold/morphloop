"""Chat resource: the natural-language entry point to the notebook (#34, #63)."""

from harness.core.chat.assistant import AssistantConfig, AssistantError, Reply, without_expected
from harness.core.chat.service import (
    SendResult,
    ThreadNotFoundError,
    find_thread,
    list_messages,
    send_message,
)
from harness.core.chat.tools import ChatTool, ToolContext, offered_tools
from harness.core.chat.view import ChatMessagesView

__all__ = [
    "AssistantConfig",
    "AssistantError",
    "ChatMessagesView",
    "ChatTool",
    "Reply",
    "SendResult",
    "ThreadNotFoundError",
    "ToolContext",
    "find_thread",
    "list_messages",
    "offered_tools",
    "send_message",
    "without_expected",
]
