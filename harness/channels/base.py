"""
Channel abstractions.

:class:`BaseChannel` defines the interface every channel must implement.
:class:`IncomingMessage` is the normalised message format passed to the handler.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable


@dataclass
class IncomingMessage:
    """
    Channel-agnostic representation of an inbound message.
    
    For multimodal messages (e.g., images), the channel stores image data
    in raw["images"] as a list of base64 data URLs:
    
        raw = {
            "images": ["data:image/jpeg;base64,..."],
            "image_count": 1,
            ...
        }
    
    The handler is responsible for converting this to the LLM provider's
    expected multimodal format.
    """

    user_id: int
    username: str | None
    text: str
    channel: str  # e.g. "telegram", "slack"
    timestamp: datetime
    # Original payload from the channel — kept for channel-specific features
    # For multimodal: raw["images"] contains list of base64 data URLs
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    
    @property
    def has_images(self) -> bool:
        """Check if this message contains images."""
        return bool(self.raw.get("images"))
    
    @property
    def images(self) -> list[str]:
        """Get list of image data URLs (base64 encoded)."""
        return self.raw.get("images", [])


# Type alias for the message handler callback
MessageHandler = Callable[[IncomingMessage], Awaitable[None]]


class BaseChannel(ABC):
    """
    Abstract base for all channels (Telegram, Slack, WhatsApp …).

    Subclasses must implement :meth:`start`, :meth:`send_message`, and
    :meth:`send_typing`. The incoming message handler is registered via
    :meth:`set_handler`.
    """

    def __init__(self) -> None:
        self._handler: MessageHandler | None = None

    def set_handler(self, handler: MessageHandler) -> None:
        """Register the coroutine that will be called for every new message."""
        self._handler = handler

    async def _dispatch(self, message: IncomingMessage) -> None:
        """Call the registered handler, if any."""
        if self._handler is not None:
            await self._handler(message)

    @abstractmethod
    async def start(self, stop_event: asyncio.Event) -> None:
        """
        Start polling / webhook loop. Should block until *stop_event* is set.
        """

    @abstractmethod
    async def send_message(self, user_id: int, text: str) -> None:
        """Send *text* to the user identified by *user_id*."""

    @abstractmethod
    async def send_typing(self, user_id: int) -> None:
        """Send a typing indicator to *user_id*."""
