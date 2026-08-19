#!/usr/bin/env python
"""
Quick smoke test for LLMProvider against a live Ollama instance.

Usage:
    python scripts/test_provider.py
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from harness.providers.llm_provider import LLMProvider, LLMProviderError


async def main() -> None:
    provider = LLMProvider.from_env()
    print(f"Model : {provider.model}")
    print(f"Base  : {provider.api_base}")
    print("Sending test message …\n")

    try:
        response = await provider.complete(
            [
                {
                    "role": "user",
                    "content": "Responda em uma frase: qual é a capital do Brasil?",
                }
            ]
        )
    except LLMProviderError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    print(f"Content : {response.content}")
    print(f"Usage   : {response.usage}")
    print(f"Tools   : {response.tool_calls}")


if __name__ == "__main__":
    asyncio.run(main())
