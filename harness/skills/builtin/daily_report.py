"""
DailyReportSkill — generates a daily meeting report.

This skill uses the LLM to produce a structured daily report in pt-BR
based on context about commits and tasks.
"""
from __future__ import annotations

import time

import structlog

from harness.skills.base import BaseSkill, SkillContext, SkillResult
from harness.providers.llm_provider import LLMProviderError

log = structlog.get_logger(__name__)


_SYSTEM_PROMPT = """\
Você é um assistente especializado em gerar relatórios de daily meeting para desenvolvedores.

Seu objetivo é produzir um relatório objetivo, claro e conciso no seguinte formato:

## Daily Report — {data}

### Finalizados
- #{ID} — {Título}: {o que foi feito em 1-2 frases}

### Em andamento
- #{ID} — {Título}: {o que está sendo feito}

### Commits do período
| Data | Hash | Tarefa | Mensagem |
|------|------|--------|----------|

### Bloqueios (se houver)
- {descrição do bloqueio}

Regras de escrita:
- Linguagem direta e simples.
- Sem jargões ou advérbios de intensidade.
- Frases curtas. Uma ideia por frase.
- Se não houver informação suficiente para alguma seção, omita essa seção.
- Responda sempre em português (pt-BR).

Se o usuário não fornecer informações específicas sobre tarefas ou commits,
pergunte quais tarefas foram trabalhadas e se há commits a incluir.
"""


class DailyReportSkill(BaseSkill):
    """Skill for generating daily meeting reports."""
    
    name = "daily_report"
    description = (
        "Gera relatório de daily meeting com tarefas concluídas, em andamento e "
        "commits Git do período. Acionar quando o usuário pedir daily, report diário "
        "ou resumo do que foi feito."
    )
    system_prompt = _SYSTEM_PROMPT
    requires_mcp = False
    
    async def execute(
        self,
        task: str,
        context: SkillContext,
    ) -> SkillResult:
        """Generate a daily report based on the task description."""
        t0 = time.monotonic()
        
        if context.llm is None:
            return SkillResult(
                content="Erro: LLM não disponível.",
                skill_name=self.name,
                success=False,
            )
        
        messages = [
            {"role": "system", "content": self.system_prompt},
            *context.history,
            {"role": "user", "content": task},
        ]
        
        try:
            response = await context.llm.complete(messages=messages, tools=None)
        except LLMProviderError as exc:
            log.error("daily_report_llm_error", error=str(exc))
            return SkillResult(
                content="Não foi possível gerar o relatório. Tente novamente.",
                skill_name=self.name,
                success=False,
                metadata={"error": str(exc)},
            )
        
        latency_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "daily_report_complete",
            latency_ms=latency_ms,
            tokens=response.usage.get("total_tokens", 0),
        )
        
        return SkillResult(
            content=response.content or "(sem resposta)",
            skill_name=self.name,
            success=True,
            metadata={
                "latency_ms": latency_ms,
                "model": context.llm.model,
                **response.usage,
            },
        )
