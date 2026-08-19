"""
Tests for ConversationRepository.
Uses unittest.mock to avoid needing a live database.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from harness.memory.repository import ConversationRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(fetchval_return=None, fetch_return=None):
    """Build a mock asyncpg pool whose acquire() returns a usable connection."""
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetchval = AsyncMock(return_value=fetchval_return)
    conn.fetch = AsyncMock(return_value=fetch_return or [])

    pool = MagicMock()
    pool.close = AsyncMock()
    # acquire() is an async context manager
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


# ---------------------------------------------------------------------------
# connect / close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_creates_pool():
    with patch("asyncpg.create_pool", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = MagicMock()
        repo = ConversationRepository()
        await repo.connect("postgresql://test/test")
        mock_create.assert_called_once()
        assert repo._pool is not None


@pytest.mark.asyncio
async def test_close_calls_pool_close():
    pool, _ = _make_pool()
    repo = ConversationRepository()
    repo._pool = pool
    await repo.close()
    pool.close.assert_called_once()


# ---------------------------------------------------------------------------
# is_allowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_allowed_returns_true_when_row_exists():
    pool, conn = _make_pool(fetchval_return=1)
    repo = ConversationRepository()
    repo._pool = pool
    assert await repo.is_allowed(12345) is True
    conn.fetchval.assert_called_once()


@pytest.mark.asyncio
async def test_is_allowed_returns_false_when_no_row():
    pool, conn = _make_pool(fetchval_return=None)
    repo = ConversationRepository()
    repo._pool = pool
    assert await repo.is_allowed(99999) is False


# ---------------------------------------------------------------------------
# append_message / get_history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_append_message_executes_insert():
    pool, conn = _make_pool()
    repo = ConversationRepository()
    repo._pool = pool
    await repo.append_message(user_id=1, role="user", content="Hello")
    conn.execute.assert_called_once()
    call_args = conn.execute.call_args[0]
    assert "INSERT INTO conversations" in call_args[0]


@pytest.mark.asyncio
async def test_get_history_returns_formatted_messages():
    fake_rows = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]
    pool, conn = _make_pool(fetch_return=fake_rows)
    repo = ConversationRepository()
    repo._pool = pool

    history = await repo.get_history(user_id=1, limit=20)

    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "Hello"}
    assert history[1] == {"role": "assistant", "content": "Hi there!"}


@pytest.mark.asyncio
async def test_get_history_empty_returns_empty_list():
    pool, _ = _make_pool(fetch_return=[])
    repo = ConversationRepository()
    repo._pool = pool
    history = await repo.get_history(user_id=999)
    assert history == []


# ---------------------------------------------------------------------------
# ensure_pool guard
# ---------------------------------------------------------------------------


def test_ensure_pool_raises_if_not_connected():
    repo = ConversationRepository()
    with pytest.raises(RuntimeError, match="connect\\(\\)"):
        repo._ensure_pool()
