"""
ShellSkill — executes shell commands with sandbox protection.

This skill allows the AI to run shell commands on the host system,
with safety checks enforced by the Sandbox and Soul configuration.

Commands are classified as:
- Auto-approved: Safe commands like ls, cat, grep (execute immediately)
- Requires confirmation: Potentially dangerous commands (ask user first)
- Blocked: Extremely dangerous commands (never execute)

Usage by the AI::

    # The AI calls this skill via tool calling
    result = await shell_skill.execute(
        task="ls -la /home/user",
        context=skill_context,
    )
"""
from __future__ import annotations

import structlog

from harness.skills.base import BaseSkill, SkillContext, SkillResult

log = structlog.get_logger(__name__)


_SYSTEM_PROMPT = """\
Você é um assistente especializado em executar comandos de terminal.

Ao receber uma solicitação:
1. Analise qual comando atende à necessidade
2. Execute o comando usando a ferramenta disponível
3. Interprete a saída e apresente de forma clara ao usuário

Regras de segurança:
- Comandos de leitura (ls, cat, grep) são seguros
- Comandos que modificam o sistema requerem confirmação do usuário
- NUNCA execute comandos destrutivos sem confirmação explícita
- Se não tiver certeza sobre a segurança, pergunte antes

Dicas:
- Use `ls -la` para listar arquivos com detalhes
- Use `cat` para ler arquivos pequenos, `head`/`tail` para arquivos grandes
- Use `grep -r` para buscar texto em arquivos
- Use `find` para localizar arquivos
- Use `pwd` para mostrar o diretório atual
"""


class ShellSkill(BaseSkill):
    """
    Skill for executing shell commands with sandbox protection.
    """
    
    name = "shell"
    description = (
        "Executa comandos de terminal/shell no sistema. "
        "Comandos seguros são executados automaticamente, "
        "comandos potencialmente perigosos requerem confirmação do usuário."
    )
    system_prompt = _SYSTEM_PROMPT
    requires_mcp = False  # Uses native subprocess, not MCP
    
    async def execute(
        self,
        task: str,
        context: SkillContext,
    ) -> SkillResult:
        """
        Execute a shell command.
        
        The task should be the shell command to execute.
        
        :param task: The shell command to run.
        :param context: Skill context with LLM and other resources.
        :returns: SkillResult with command output.
        """
        from harness.core.sandbox import Sandbox
        from harness.soul import load_soul
        
        command = task.strip()
        
        if not command:
            return SkillResult(
                content="Nenhum comando especificado.",
                skill_name=self.name,
                success=False,
            )
        
        # Load soul for sandbox configuration
        # TODO: Pass soul via context.metadata instead of loading here
        soul = context.metadata.get("soul")
        if soul is None:
            soul = load_soul("config/soul.yaml")
        
        sandbox = Sandbox(soul)
        
        # Check permission
        permission = sandbox.check_command(command)
        
        if permission.level.value == "blocked":
            return SkillResult(
                content=permission.message or f"Comando bloqueado: {command}",
                skill_name=self.name,
                success=False,
            )
        
        if permission.requires_confirmation:
            return SkillResult(
                content="",
                skill_name=self.name,
                success=True,
                requires_confirmation=True,
                confirmation_message=permission.message,
                metadata={"pending_command": command},
            )
        
        # Execute the command
        result = await sandbox.execute(command, timeout=30.0)
        
        # Format output
        if result.success:
            output = f"```\n$ {command}\n{result.output}\n```"
            if result.return_code != 0:
                output += f"\n(exit code: {result.return_code})"
        else:
            output = f"❌ Comando falhou:\n```\n$ {command}\n{result.stderr or result.stdout}\n```"
        
        return SkillResult(
            content=output,
            skill_name=self.name,
            success=result.success,
            metadata={
                "command": command,
                "return_code": result.return_code,
                "stdout_len": len(result.stdout),
                "stderr_len": len(result.stderr),
            },
        )
    
    async def execute_with_confirmation(
        self,
        command: str,
        context: SkillContext,
    ) -> SkillResult:
        """
        Execute a command that was previously pending confirmation.
        
        Call this after the user confirms they want to run the command.
        
        :param command: The confirmed command to run.
        :param context: Skill context.
        :returns: SkillResult with command output.
        """
        from harness.core.sandbox import Sandbox
        from harness.soul import load_soul
        
        soul = context.metadata.get("soul")
        if soul is None:
            soul = load_soul("config/soul.yaml")
        
        sandbox = Sandbox(soul)
        
        log.info("executing_confirmed_command", command=command)
        result = await sandbox.execute(command, timeout=30.0)
        
        if result.success:
            output = f"✅ Comando executado:\n```\n$ {command}\n{result.output}\n```"
        else:
            output = f"❌ Comando falhou:\n```\n$ {command}\n{result.stderr or result.stdout}\n```"
        
        return SkillResult(
            content=output,
            skill_name=self.name,
            success=result.success,
            metadata={
                "command": command,
                "return_code": result.return_code,
                "confirmed": True,
            },
        )
