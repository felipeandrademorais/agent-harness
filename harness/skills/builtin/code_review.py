"""
CodeReviewSkill — senior code review specialist.

Reviews code for quality, security, and best practices.
Can work with or without MCP GitLab integration.
"""

from __future__ import annotations

import json
import time
from typing import Any

import structlog

from harness.providers.llm_provider import LLMProviderError
from harness.skills.base import BaseSkill, SkillContext, SkillResult

log = structlog.get_logger(__name__)

_MAX_TOOL_ITERATIONS = 5

_SYSTEM_PROMPT = """\
Você é um engenheiro de software sênior realizando code review.

Ao revisar código, você deve:
1. Identificar problemas de **segurança** (injeção, exposição de segredos, autenticação)
2. Identificar problemas de **qualidade** (legibilidade, complexidade, duplicação)
3. Identificar problemas de **performance** (queries N+1, operações desnecessárias)
4. Sugerir **melhorias** (boas práticas, padrões do projeto)

Formato do relatório:

## Code Review — {branch/MR}

### 🔴 Crítico (deve ser corrigido antes do merge)
- **Arquivo:linha** — descrição do problema + sugestão de correção

### 🟡 Importante (correção recomendada)
- **Arquivo:linha** — descrição + sugestão

### 🟢 Sugestão (melhorias opcionais)
- **Arquivo:linha** — sugestão

### ✅ Pontos positivos
- O que foi bem feito

**Resumo:** {1-2 frases sobre a qualidade geral}

Regras:
- Seja objetivo e construtivo.
- Cite o arquivo e a linha específica sempre que possível.
- Não invente problemas — revise apenas o que está no diff.
- Responda em português (pt-BR).
"""


class CodeReviewSkill(BaseSkill):
    """Skill for performing code reviews."""

    name = "code_review"
    description = (
        "Realiza code review de merge requests, diffs ou trechos de código. "
        "Analisa segurança, qualidade e performance. Acionar quando o usuário pedir "
        "code review, revisão de código, revisar MR, revisar PR ou analisar código."
    )
    system_prompt = _SYSTEM_PROMPT
    requires_mcp = False  # Can work without MCP (inline code review)
    mcp_tools = ["gitlab_get_mr", "gitlab_get_diff", "gitlab_get_file"]

    async def execute(
        self,
        task: str,
        context: SkillContext,
    ) -> SkillResult:
        """
        Execute code review.

        If MCP is available, can fetch diffs from GitLab.
        Otherwise, reviews code pasted directly in the task.
        """
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

        # Check if we have MCP for GitLab
        has_mcp_gitlab = context.mcp is not None and any(
            context.mcp.get_tool_server(t)
            for t in ["gitlab_get_mr", "get_merge_request"]
        )

        if not has_mcp_gitlab:
            # No MCP — review inline code or provide guidance
            if len(task) < 100:
                return SkillResult(
                    content=(
                        "⚠️ *MCP GitLab não configurado*\n\n"
                        "Para buscar MRs automaticamente, configure o MCP GitLab.\n\n"
                        "Posso fazer code review se você **colar o código diretamente** na mensagem."
                    ),
                    skill_name=self.name,
                    success=True,
                    metadata={"stub": True},
                )

            # User pasted code inline — review without tools
            try:
                response = await context.llm.complete(messages=messages, tools=None)
                return SkillResult(
                    content=response.content or "(sem resposta)",
                    skill_name=self.name,
                    success=True,
                    metadata={"model": context.llm.model, **response.usage},
                )
            except LLMProviderError as exc:
                return SkillResult(
                    content=f"Erro ao processar o review: {exc}",
                    skill_name=self.name,
                    success=False,
                    metadata={"error": str(exc)},
                )

        # With MCP GitLab — use tool calling
        tool_defs = await context.mcp.list_all_tools()
        iteration = 0

        for iteration in range(_MAX_TOOL_ITERATIONS):
            try:
                response = await context.llm.complete(
                    messages=messages,
                    tools=tool_defs or None,
                )
            except LLMProviderError as exc:
                log.error("code_review_llm_error", error=str(exc))
                return SkillResult(
                    content="Erro ao processar o review. Tente novamente.",
                    skill_name=self.name,
                    success=False,
                    metadata={"error": str(exc)},
                )

            if not response.tool_calls:
                break

            # Add assistant message with tool calls
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

            for tc in response.tool_calls:
                result = await context.mcp.call_tool(tc.name, tc.arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result.content,
                    }
                )

        latency_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "code_review_complete", latency_ms=latency_ms, iterations=iteration + 1
        )

        return SkillResult(
            content=response.content or "(sem resposta)",
            skill_name=self.name,
            success=True,
            metadata={
                "latency_ms": latency_ms,
                "model": context.llm.model,
                "iterations": iteration + 1,
                **response.usage,
            },
        )
