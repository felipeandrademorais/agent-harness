"""
ConversationRepository — asyncpg-backed persistence for conversation history
and user whitelist.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import asyncpg
import structlog

log = structlog.get_logger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class ConversationRepository:
    """Manages the asyncpg connection pool and all DB operations."""

    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self, database_url: str) -> None:
        """Create the connection pool."""
        self._pool = await asyncpg.create_pool(
            dsn=database_url,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
        log.info("db_pool_created")

    async def close(self) -> None:
        """Close all connections in the pool."""
        if self._pool:
            await self._pool.close()
            log.info("db_pool_closed")

    def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("ConversationRepository.connect() was not called.")
        return self._pool

    # ------------------------------------------------------------------
    # Migrations
    # ------------------------------------------------------------------

    async def run_migrations(self) -> None:
        """Execute SQL migration files that have not yet been applied."""
        pool = self._ensure_pool()

        # Ensure the tracking table exists first (idempotent)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version    TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

        migration_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
        for path in migration_files:
            version = path.stem
            async with pool.acquire() as conn:
                already_applied = await conn.fetchval(
                    "SELECT 1 FROM schema_migrations WHERE version = $1", version
                )
                if already_applied:
                    log.debug("migration_skipped", version=version)
                    continue

                sql = path.read_text()
                await conn.execute(sql)
                log.info("migration_applied", version=version)

    # ------------------------------------------------------------------
    # Whitelist
    # ------------------------------------------------------------------

    async def is_allowed(self, user_id: int) -> bool:
        """Return True if *user_id* is in the allowed_users table."""
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchval(
                "SELECT 1 FROM allowed_users WHERE user_id = $1", user_id
            )
        return row is not None

    async def add_allowed_user(self, user_id: int, username: str | None = None) -> None:
        """Insert a user into the whitelist (idempotent)."""
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO allowed_users (user_id, username)
                VALUES ($1, $2)
                ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username
                """,
                user_id,
                username,
            )

    # ------------------------------------------------------------------
    # Conversation history
    # ------------------------------------------------------------------

    async def get_history(
        self, user_id: int, limit: int = 20
    ) -> list[dict[str, Any]]:
        """
        Return the last *limit* messages for *user_id*, oldest-first,
        in the OpenAI messages format: [{role, content}, ...].
        """
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT role, content
                FROM (
                    SELECT role, content, created_at
                    FROM conversations
                    WHERE user_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2
                ) sub
                ORDER BY created_at ASC
                """,
                user_id,
                limit,
            )
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    async def append_message(
        self,
        user_id: int,
        role: str,
        content: str,
        agent_name: str | None = None,
    ) -> None:
        """Persist a single message to the conversations table."""
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO conversations (user_id, role, content, agent_name)
                VALUES ($1, $2, $3, $4)
                """,
                user_id,
                role,
                content,
                agent_name,
            )

    async def clear_history(self, user_id: int) -> None:
        """Delete all conversation history for a user (useful for /reset)."""
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM conversations WHERE user_id = $1", user_id
            )
