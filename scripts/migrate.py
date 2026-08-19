#!/usr/bin/env python
"""Run all pending SQL migrations against DATABASE_URL."""

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

# Allow running from project root without installing the package
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from harness.memory.repository import ConversationRepository


async def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "ERROR: DATABASE_URL is not set. Copy .env.example to .env and fill in values."
        )
        sys.exit(1)

    print(f"Connecting to {database_url} ...")
    repo = ConversationRepository()
    await repo.connect(database_url)
    await repo.run_migrations()
    await repo.close()
    print("Migrations complete.")


if __name__ == "__main__":
    asyncio.run(main())
