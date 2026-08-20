"""
Dispatcher — bridges the channel and the agent.

Receives a normalised IncomingMessage from any channel, forwards it to the
PrimaryAgent, and returns the response back to the channel. All error
handling lives here so neither the channel nor the agent needs to worry
about it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

import structlog

from harness.channels.base import BaseChannel, IncomingMessage
from harness.core.exceptions import BOUNDARY_ERRORS

if TYPE_CHECKING:
    from harness.memory.repository import ConversationRepository

log = structlog.get_logger(__name__)

_TIMEOUT_SECONDS = 120
_TIMEOUT_MESSAGE = "⏱ A IA demorou muito para responder. Tente novamente."
_ERROR_MESSAGE = "⚠️ Desculpe, ocorreu um erro ao processar sua mensagem. Tente novamente."


class MessageProcessor(Protocol):
    """Protocol for anything that can process an IncomingMessage."""

    async def process(self, message: IncomingMessage) -> str: ...


class Dispatcher:
    """
    Glue between a :class:`BaseChannel` and the :class:`PrimaryAgent`.

    Single responsibility: receive → process → respond, with error handling.
    """

    def __init__(
        self,
        primary: MessageProcessor,
        channel: BaseChannel,
        memory: ConversationRepository,
        *,
        # Legacy support: also accept 'orchestrator' kwarg
        orchestrator: MessageProcessor | None = None,
    ) -> None:
        # Support both primary= and orchestrator= for backwards compatibility
        self._processor = primary if primary is not None else orchestrator
        if self._processor is None:
            raise ValueError("Either 'primary' or 'orchestrator' must be provided")

        self._channel = channel
        self._memory = memory

    async def handle_message(self, message: IncomingMessage) -> None:
        """
        Entry point called by the channel for every incoming message.

        Sends a typing indicator, processes the message through the
        agent, and delivers the response back to the channel.
        """
        log.info(
            "dispatcher_received",
            user_id=message.user_id,
            channel=message.channel,
            text_len=len(message.text),
            has_images=message.has_images,
        )

        # Typing indicator — fire-and-forget, don't block on failure
        asyncio.create_task(self._channel.send_typing(message.user_id))

        try:
            response = await asyncio.wait_for(
                self._processor.process(message),
                timeout=_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            log.warning("dispatcher_timeout", user_id=message.user_id)
            response = _TIMEOUT_MESSAGE
        except BOUNDARY_ERRORS as exc:
            log.exception(
                "dispatcher_error",
                user_id=message.user_id,
                error=str(exc),
            )
            response = _ERROR_MESSAGE

        await self._channel.send_message(message.user_id, response)
        log.info(
            "dispatcher_replied",
            user_id=message.user_id,
            response_len=len(response),
        )
