"""Tests for TelegramChannel — whitelist middleware and message splitting."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

import pytest

from harness.channels.telegram import TelegramChannel, WhitelistMiddleware, _split_message
from harness.channels.base import IncomingMessage


# ---------------------------------------------------------------------------
# _split_message
# ---------------------------------------------------------------------------


def test_split_message_short_text_unchanged():
    chunks = _split_message("Hello world", max_len=100)
    assert chunks == ["Hello world"]


def test_split_message_exact_max_len():
    text = "a" * 4096
    chunks = _split_message(text, max_len=4096)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_split_message_long_text_splits():
    text = "a" * 5000
    chunks = _split_message(text, max_len=4096)
    assert len(chunks) == 2
    assert all(len(c) <= 4096 for c in chunks)
    assert "".join(chunks) == text


def test_split_message_prefers_newline_boundary():
    # 4050 chars + newline + 100 chars
    text = "x" * 4050 + "\n" + "y" * 100
    chunks = _split_message(text, max_len=4096)
    # First chunk should end at the newline
    assert chunks[0] == "x" * 4050
    assert chunks[1] == "y" * 100


# ---------------------------------------------------------------------------
# WhitelistMiddleware
# ---------------------------------------------------------------------------


def _make_update(user_id: int, text: str = "hello", is_start: bool = False):
    """Build a minimal fake aiogram Update with a message."""
    user = MagicMock()
    user.id = user_id

    message = MagicMock()
    message.from_user = user
    message.text = "/start" if is_start else text
    message.answer = AsyncMock()

    update = MagicMock()
    update.message = message
    return update


@pytest.mark.asyncio
async def test_whitelist_middleware_allows_authorised_user():
    memory = MagicMock()
    memory.is_allowed = AsyncMock(return_value=True)
    mw = WhitelistMiddleware(memory)

    update = _make_update(user_id=111)
    handler = AsyncMock()

    await mw(handler, update, {})

    handler.assert_called_once_with(update, {})
    update.message.answer.assert_not_called()


@pytest.mark.asyncio
async def test_whitelist_middleware_blocks_unauthorised_user():
    memory = MagicMock()
    memory.is_allowed = AsyncMock(return_value=False)
    mw = WhitelistMiddleware(memory)

    update = _make_update(user_id=999)
    handler = AsyncMock()

    await mw(handler, update, {})

    handler.assert_not_called()
    update.message.answer.assert_called_once()
    assert "não autorizado" in update.message.answer.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_whitelist_middleware_allows_start_command_for_anyone():
    """
    /start must pass through even for users not in the whitelist,
    so the bot can reply with an informative message.
    """
    memory = MagicMock()
    memory.is_allowed = AsyncMock(return_value=False)
    mw = WhitelistMiddleware(memory)

    update = _make_update(user_id=999, is_start=True)
    handler = AsyncMock()

    await mw(handler, update, {})

    # Handler IS called — the TelegramChannel /start handler decides the reply
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_whitelist_middleware_passes_non_message_updates_through():
    memory = MagicMock()
    mw = WhitelistMiddleware(memory)

    update = MagicMock()
    update.message = None  # e.g. a callback query
    handler = AsyncMock()

    await mw(handler, update, {})

    handler.assert_called_once_with(update, {})
