"""
TelegramChannel — aiogram 3.x implementation of BaseChannel.

Features:
- Whitelist middleware: only allowed user IDs can interact.
- /start  — welcome message.
- /help   — lists available skills dynamically.
- /reset  — clears conversation history for the user.
- Typing indicator while the handler processes the message.
- Long messages (>4096 chars) are split automatically.
- Multimodal support: processes photos and sends them to the LLM.
"""

from __future__ import annotations

import asyncio
import base64
import io
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command
from aiogram.types import Message, PhotoSize, Update

from harness.channels.base import BaseChannel, IncomingMessage
from harness.core.exceptions import BOUNDARY_ERRORS

if TYPE_CHECKING:
    from harness.memory.repository import ConversationRepository
    from harness.skills.registry import SkillRegistry

log = structlog.get_logger(__name__)

try:
    from aiogram.exceptions import TelegramAPIError
except ImportError:  # pragma: no cover
    TelegramAPIError = BOUNDARY_ERRORS  # type: ignore[misc,assignment]

_TELEGRAM_MAX_LEN = 4096


def escape_md(text: str) -> str:
    """Escape special MarkdownV2 characters."""
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in text)


class WhitelistMiddleware:
    """
    Aiogram middleware that rejects messages from users not in the whitelist.
    Passes the check for /start so new users can receive an informative reply.
    """

    def __init__(self, memory: ConversationRepository) -> None:
        self._memory = memory

    async def __call__(self, handler, event: Update, data: dict[str, Any]):
        message: Message | None = event.message
        if message is None:
            # Allow non-message updates (e.g. callback queries) through unchanged
            return await handler(event, data)

        user_id = message.from_user.id if message.from_user else None

        # Always allow /start so the bot can respond meaningfully
        if message.text and message.text.strip().startswith("/start"):
            return await handler(event, data)

        if user_id is None or not await self._memory.is_allowed(user_id):
            await message.answer("⛔ Acesso não autorizado.")
            log.warning("unauthorized_access", user_id=user_id)
            return  # Do NOT call handler

        return await handler(event, data)


class TelegramChannel(BaseChannel):
    """Telegram channel built on aiogram 3.x with multimodal support."""

    def __init__(
        self,
        token: str,
        memory: ConversationRepository,
        skills: SkillRegistry | None = None,
    ) -> None:
        super().__init__()
        self._token = token
        self._memory = memory
        self._skills = skills

        self._bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
        )
        self._dp = Dispatcher()
        self._dp.update.outer_middleware(WhitelistMiddleware(memory))

        # Register handlers
        self._dp.message.register(self._handle_start, Command("start"))
        self._dp.message.register(self._handle_help, Command("help"))
        self._dp.message.register(self._handle_reset, Command("reset"))
        # Handle photos (with or without caption)
        self._dp.message.register(self._handle_photo, F.photo)
        self._dp.message.register(self._handle_message, F.text)

    async def start(self, stop_event: asyncio.Event) -> None:
        """Start long-polling until *stop_event* is set."""
        log.info("telegram_polling_start")
        polling_task = asyncio.create_task(
            self._dp.start_polling(self._bot, handle_signals=False)
        )
        await stop_event.wait()
        await self._dp.stop_polling()
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
        await self._bot.session.close()
        log.info("telegram_polling_stopped")

    async def send_message(self, user_id: int, text: str) -> None:
        """Send *text* to *user_id* as plain text, splitting if needed."""
        for chunk in _split_message(text):
            try:
                await self._bot.send_message(user_id, chunk, parse_mode=None)
            except TelegramAPIError as exc:
                log.error("telegram_send_error", user_id=user_id, error=str(exc))

    async def send_typing(self, user_id: int) -> None:
        """Send a typing indicator."""
        try:
            await self._bot.send_chat_action(user_id, ChatAction.TYPING)
        except TelegramAPIError as exc:
            log.warning("telegram_typing_error", user_id=user_id, error=str(exc))

    async def _handle_start(self, message: Message) -> None:
        user = message.from_user
        name = user.first_name if user else "usuário"
        user_id = user.id if user else 0

        if await self._memory.is_allowed(user_id):
            await message.answer(
                f"Olá, *{escape_md(name)}*\\! Harness ativo\\. ✅\n\n"
                "Use /help para ver as skills disponíveis\\.\n"
                "Você pode enviar texto ou imagens\\!",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        else:
            await message.answer(
                f"Olá, {name}\\! Você não está na lista de usuários autorizados\\. "
                "Entre em contato com o administrador\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )

    async def _handle_help(self, message: Message) -> None:
        if self._skills and self._skills.list_all():
            skills_text = "\n".join(
                f"• *{s.name}* — {s.description}" for s in self._skills.list_all()
            )
            text = f"*Skills disponíveis:*\n\n{skills_text}"
        else:
            text = "Nenhuma skill registrada ainda."
        await message.answer(text)

    async def _handle_reset(self, message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        await self._memory.clear_history(user_id)
        await message.answer("Histórico de conversa apagado. ✅")
        log.info("history_cleared", user_id=user_id)

    async def _handle_message(self, message: Message) -> None:
        """Handle text-only messages."""
        if not message.text or not message.from_user:
            return

        user = message.from_user
        incoming = IncomingMessage(
            user_id=user.id,
            username=user.username,
            text=message.text,
            channel="telegram",
            timestamp=datetime.now(tz=UTC),
            raw={
                "message_id": message.message_id,
                "chat_id": message.chat.id,
            },
        )

        log.info(
            "telegram_message_received",
            user_id=user.id,
            username=user.username,
            text_length=len(message.text),
        )

        await self._dispatch(incoming)

    async def _handle_photo(self, message: Message) -> None:
        """Handle messages with photos (multimodal)."""
        if not message.from_user:
            return

        user = message.from_user

        # Get the caption (text accompanying the photo) or default prompt
        caption = message.caption or "Descreva esta imagem."

        # Download the largest photo
        photos = message.photo
        if not photos:
            return

        # photos is sorted by size, last is largest
        largest_photo: PhotoSize = photos[-1]

        try:
            # Download the photo
            image_bytes = await self._download_photo(largest_photo.file_id)

            # Convert to base64 data URL
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
            image_data_url = f"data:image/jpeg;base64,{image_b64}"

            log.info(
                "telegram_photo_received",
                user_id=user.id,
                file_id=largest_photo.file_id,
                size=len(image_bytes),
                width=largest_photo.width,
                height=largest_photo.height,
            )

        except TelegramAPIError as exc:
            log.error("telegram_photo_download_error", error=str(exc))
            await message.answer("❌ Erro ao processar a imagem. Tente novamente.")
            return

        incoming = IncomingMessage(
            user_id=user.id,
            username=user.username,
            text=caption,
            channel="telegram",
            timestamp=datetime.now(tz=UTC),
            raw={
                "message_id": message.message_id,
                "chat_id": message.chat.id,
                "images": [image_data_url],  # List of base64 data URLs
                "image_count": 1,
            },
        )

        await self._dispatch(incoming)

    async def _download_photo(self, file_id: str) -> bytes:
        """Download a photo by file_id and return the bytes."""
        file = await self._bot.get_file(file_id)
        if not file.file_path:
            raise ValueError("No file_path in Telegram response")

        # Download file content
        file_bytes = io.BytesIO()
        await self._bot.download_file(file.file_path, file_bytes)
        file_bytes.seek(0)
        return file_bytes.read()


def _split_message(text: str, max_len: int = _TELEGRAM_MAX_LEN) -> list[str]:
    """Split *text* into chunks of at most *max_len* characters."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    while text:
        candidate = text[:max_len]
        # Try to split at a newline to avoid cutting mid-sentence
        split_at = candidate.rfind("\n")
        if split_at > max_len // 2:
            chunk = candidate[:split_at]
            # Advance past the newline so it is not included in the next chunk
            text = text[split_at + 1 :]
        else:
            chunk = candidate
            text = text[max_len:]
        chunks.append(chunk)
    return chunks
