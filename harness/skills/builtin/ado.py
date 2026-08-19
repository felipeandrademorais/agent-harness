"""
ADOSkill — Azure DevOps specialist.

Queries and manages work items, pipelines, repositories, and pull requests
using MCP ADO integration.
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
Você é um assistente especializado em Azure DevOps (ADO).

Você tem acesso a ferramentas para:
- Consultar e criar work items (tarefas, bugs, user stories, tickets)
- Listar e atualizar pipelines e builds
- Consultar pull requests e repositórios
- Buscar commits e histórico de código

Diretrizes:
- Responda sempre em português (pt-BR).
- Seja objetivo: apresente os dados diretamente, sem introduções desnecessárias.
- Quando listar work items, mostre: ID, título, estado e responsável.
- Quando não encontrar informações, diga claramente em vez de inventar.
- Use as ferramentas disponíveis para buscar dados antes de responder.
"""


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


class ADOSkill(BaseSkill):
    """Skill for Azure DevOps integration."""

    name = "ado"
    description = (
        "Consulta e gerencia itens do Azure DevOps: work items, tarefas, bugs, "
        "pipelines, pull requests e repositórios. Acionar quando o usuário perguntar "
        "sobre tarefas, board, sprints, commits ou ADO."
    )
    system_prompt = _SYSTEM_PROMPT
    requires_mcp = True
    mcp_tools: ClassVar[list[str]] = [
        "wit_work_item",
        "wit_query",
        "repo_pull_request",
        "pipelines_build",
    ]

    def _mcp_unavailable_result(self) -> SkillResult:
        return SkillResult(
            content=(
                "⚠️ *MCP ADO não configurado*\n\n"
                "Para usar o agente ADO, configure a variável de ambiente `MCP_ADO_COMMAND` "
                "com o comando para iniciar o servidor MCP.\n\n"
                "Exemplo: `MCP_ADO_COMMAND=python -m mcp_ado_server`\n\n"
                "Por enquanto, posso responder perguntas gerais sobre Azure DevOps "
                "sem acesso real aos dados."
            ),
            skill_name=self.name,
            success=True,
            metadata={"stub": True},
        )

    async def _run_tool_loop(
        self,
        context: SkillContext,
        messages: list[dict[str, Any]],
        tool_defs: list[dict[str, Any]],
        t0: float,
    ) -> SkillResult:
        """Run the MCP tool-calling loop for ADO operations."""
        iteration = 0
        response = None

        for iteration in range(_MAX_TOOL_ITERATIONS):
            try:
                response = await context.llm.complete(
                    messages=messages,
                    tools=tool_defs or None,
                )
            except LLMProviderError as exc:
                log.error("ado_skill_llm_error", error=str(exc), iteration=iteration)
                return SkillResult(
                    content="Erro ao processar a solicitação. Tente novamente.",
                    skill_name=self.name,
                    success=False,
                    metadata={"error": str(exc)},
                )

            if not response.tool_calls:
                break

            _append_assistant_tool_calls(messages, response)

            for tc in response.tool_calls:
                result = await context.mcp.call_tool(tc.name, tc.arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result.content,
                    }
                )
                log.debug(
                    "ado_tool_called",
                    tool=tc.name,
                    is_error=result.is_error,
                    iteration=iteration,
                )

        latency_ms = int((time.monotonic() - t0) * 1000)
        log.info("ado_skill_complete", latency_ms=latency_ms, iterations=iteration + 1)

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
        """Execute ADO operations."""
        t0 = time.monotonic()

        if context.llm is None:
            return SkillResult(
                content="Erro: LLM não disponível.",
                skill_name=self.name,
                success=False,
            )

        has_mcp_ado = context.mcp is not None and any(
            context.mcp.get_tool_server(t)
            for t in ["wit_work_item", "wit_query", "search_workitem"]
        )

        if not has_mcp_ado:
            return self._mcp_unavailable_result()

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            *context.history,
            {"role": "user", "content": task},
        ]

        tool_defs = await context.mcp.list_all_tools()
        return await self._run_tool_loop(context, messages, tool_defs, t0)
