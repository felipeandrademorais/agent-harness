"""
SQLSkill — PostgreSQL specialist.

Executes queries, analyzes schemas, and explains query plans.
Only read operations (SELECT, EXPLAIN) are permitted.
"""

from __future__ import annotations

import json
import time
from typing import Any, ClassVar

import structlog

from harness.providers.llm_provider import LLMProviderError
from harness.skills.base import BaseSkill, SkillContext, SkillResult

log = structlog.get_logger(__name__)

_MAX_TOOL_ITERATIONS = 5

_SYSTEM_PROMPT = """\
Você é um especialista em PostgreSQL com foco em consultas, performance e modelagem de dados.

Você tem acesso a ferramentas para:
- Listar tabelas e views do banco
- Descrever a estrutura de tabelas (colunas, tipos, índices)
- Executar consultas SELECT (somente leitura)
- Analisar planos de execução com EXPLAIN ANALYZE

**REGRAS DE SEGURANÇA — OBRIGATÓRIAS:**
- Nunca execute CREATE, DROP, INSERT, UPDATE, DELETE, ALTER, TRUNCATE.
- Apenas SELECT, EXPLAIN e EXPLAIN ANALYZE são permitidos.
- Se o usuário pedir uma operação de escrita, explique que não é permitido neste contexto.

**Como responder:**
- Para consultas: mostre o SQL gerado e o resultado em tabela markdown quando possível.
- Para análise de performance: explique o plano de execução em linguagem simples.
- Para modelagem: sugira índices e otimizações com justificativa.
- Avise quando uma query não foi testada no banco (⚠️ Query não testada).
- Responda em português (pt-BR).
"""


def _is_write_operation(tool_name: str, arguments: dict[str, Any]) -> bool:
    """Check if a tool call would perform a write operation."""
    write_keywords = {
        "insert",
        "update",
        "delete",
        "drop",
        "create",
        "alter",
        "truncate",
        "grant",
        "revoke",
    }

    name_lower = tool_name.lower()
    if any(kw in name_lower for kw in write_keywords):
        return True

    query = str(arguments.get("query", "")).lower().strip()
    if query:
        first_word = query.split()[0] if query.split() else ""
        if first_word in write_keywords:
            return True

    return False


def _append_assistant_tool_calls(messages: list[dict[str, Any]], response: Any) -> None:
    """Append an assistant message carrying tool_calls to *messages*."""
    messages.append(
        {
            "role": "assistant",
            "content": response.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in response.tool_calls
            ],
        }
    )


class SQLSkill(BaseSkill):
    """Skill for PostgreSQL queries and analysis."""

    name = "sql"
    description = (
        "Especialista em SQL e PostgreSQL. Gera queries, analisa performance, "
        "descreve tabelas e explica planos de execução. Acionar quando o usuário "
        "pedir queries SQL, análise de banco, índices, views ou otimização de consultas."
    )
    system_prompt = _SYSTEM_PROMPT
    requires_mcp = True
    mcp_tools: ClassVar[list[str]] = [
        "postgres_query",
        "postgres_list_tables",
        "postgres_describe_table",
    ]

    async def _execute_without_mcp(
        self,
        task: str,
        context: SkillContext,
        messages: list[dict[str, Any]],
    ) -> SkillResult:
        """Answer without database access when MCP PostgreSQL is unavailable."""
        try:
            response = await context.llm.complete(messages=messages, tools=None)
            content = response.content or "(sem resposta)"

            needs_warning = any(
                kw in task.lower() for kw in ("execute", "roda", "executar", "resultado", "retorna")
            )
            if needs_warning:
                content = "⚠️ *Query não testada — MCP PostgreSQL não configurado.*\n\n" + content

            return SkillResult(
                content=content,
                skill_name=self.name,
                success=True,
                metadata={"stub": True, "model": context.llm.model},
            )
        except LLMProviderError as exc:
            return SkillResult(
                content=f"Erro ao processar: {exc}",
                skill_name=self.name,
                success=False,
                metadata={"error": str(exc)},
            )

    async def _run_tool_loop(
        self,
        context: SkillContext,
        messages: list[dict[str, Any]],
        tool_defs: list[dict[str, Any]],
        t0: float,
    ) -> SkillResult:
        """Run the MCP tool-calling loop for SQL operations."""
        iteration = 0
        response = None

        for iteration in range(_MAX_TOOL_ITERATIONS):
            try:
                response = await context.llm.complete(
                    messages=messages,
                    tools=tool_defs or None,
                )
            except LLMProviderError as exc:
                log.error("sql_skill_llm_error", error=str(exc))
                return SkillResult(
                    content="Erro ao processar a consulta. Tente novamente.",
                    skill_name=self.name,
                    success=False,
                    metadata={"error": str(exc)},
                )

            if not response.tool_calls:
                break

            _append_assistant_tool_calls(messages, response)

            for tc in response.tool_calls:
                if _is_write_operation(tc.name, tc.arguments):
                    log.warning("sql_skill_blocked_write", tool=tc.name, args=tc.arguments)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": (
                                "Operação de escrita bloqueada por segurança. "
                                "Apenas SELECT é permitido."
                            ),
                        }
                    )
                    continue

                result = await context.mcp.call_tool(tc.name, tc.arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result.content,
                    }
                )

        latency_ms = int((time.monotonic() - t0) * 1000)
        log.info("sql_skill_complete", latency_ms=latency_ms, iterations=iteration + 1)

        return SkillResult(
            content=(response.content if response else None) or "(sem resposta)",
            skill_name=self.name,
            success=True,
            metadata={
                "latency_ms": latency_ms,
                "model": context.llm.model,
                "iterations": iteration + 1,
                **(response.usage if response else {}),
            },
        )

    async def execute(
        self,
        task: str,
        context: SkillContext,
    ) -> SkillResult:
        """Execute SQL operations."""
        t0 = time.monotonic()

        if context.llm is None:
            return SkillResult(
                content="Erro: LLM não disponível.",
                skill_name=self.name,
                success=False,
            )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            *context.history,
            {"role": "user", "content": task},
        ]

        has_mcp_postgres = context.mcp is not None and any(
            context.mcp.get_tool_server(t) for t in ["postgres_query", "query", "list_tables"]
        )

        if not has_mcp_postgres:
            return await self._execute_without_mcp(task, context, messages)

        tool_defs = await context.mcp.list_all_tools()
        return await self._run_tool_loop(context, messages, tool_defs, t0)
