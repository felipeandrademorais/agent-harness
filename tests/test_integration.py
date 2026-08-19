"""
Integration tests for Agent Harness.

These tests validate the three core capabilities without depending on Telegram:
1. Shell command execution via ShellSkill
2. Sub-agent spawning via AgentFactory
3. MCP tool execution via MCPManager

All tests use:
- Mocked LLMProvider with predetermined responses
- Real LangGraph StateGraph (build_harness_graph)
- Real skills (ShellSkill)
- Mocked MCPManager (for MCP tests)
- MemorySaver as checkpointer
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from harness.agents.factory import AgentFactory
from harness.agents.graph import build_harness_graph
from harness.agents.tools_adapter import build_all_langchain_tools, create_mcp_tool
from harness.providers.chat_model import LiteLLMChatModel
from harness.providers.llm_provider import LLMResponse, ToolCall
from harness.providers.mcp_client import MCPToolResult
from harness.skills.builtin.shell import ShellSkill
from harness.skills.registry import SkillRegistry
from harness.soul.loader import Soul

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_llm_provider() -> MagicMock:
    """Create a mock LLM provider with async complete method."""
    provider = MagicMock()
    provider.complete = AsyncMock()
    return provider


@pytest.fixture
def test_soul() -> Soul:
    """Create a Soul with test-appropriate patterns for sandbox classification."""
    return Soul(
        name="TestBot",
        mood="professional",
        tone="Direct and helpful.",
        language="pt-BR",
        # Commands that execute automatically without confirmation
        auto_approve_patterns=[
            "echo*",
            "ls*",
            "pwd",
            "cat*",
            "head*",
            "tail*",
            "grep*",
            "find*",
            "which*",
            "whoami",
            "date",
            "uname*",
        ],
        # Commands that require user confirmation before execution
        require_confirmation_patterns=[
            "rm*",
            "mv*",
            "cp*",
            "chmod*",
            "chown*",
            "docker*",
            "kubectl*",
            "git push*",
            "git reset*",
        ],
    )


@pytest.fixture
def skill_registry() -> SkillRegistry:
    """Create a skill registry with ShellSkill registered."""
    registry = SkillRegistry()
    registry.register(ShellSkill())
    return registry


@pytest.fixture
def mock_mcp_manager() -> MagicMock:
    """
    Create a mock MCPManager that simulates MCP tools.

    Simulates a 'read_file' tool that returns file contents.
    """
    manager = MagicMock()

    # Configure total_tools property
    manager.total_tools = 1

    # Configure get_tool_server to return server name for our mock tool
    manager.get_tool_server = MagicMock(
        side_effect=lambda name: "mock_server" if name == "read_file" else None
    )

    # Configure list_all_tools to return OpenAI-compatible tool definitions
    manager.list_all_tools = AsyncMock(
        return_value=[
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read contents of a file",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Path to the file to read",
                            }
                        },
                        "required": ["path"],
                    },
                },
            }
        ]
    )

    # Configure call_tool to return mock results
    manager.call_tool = AsyncMock(
        return_value=MCPToolResult(
            content="File contents: Hello from MCP!",
            is_error=False,
        )
    )

    return manager


async def create_test_graph(
    mock_llm_provider: MagicMock,
    skill_registry: SkillRegistry,
    test_soul: Soul,
    mock_mcp_manager: MagicMock | None = None,
    factory: AgentFactory | None = None,
):
    """
    Create a test graph with mocked LLM and real skills.

    Returns tuple of (compiled_graph, checkpointer).
    """
    chat_model = LiteLLMChatModel(provider=mock_llm_provider)

    tools = build_all_langchain_tools(
        skills=skill_registry,
        mcp_manager=mock_mcp_manager,
        factory=factory,
        llm=mock_llm_provider,
        soul=test_soul,
    )

    # Add MCP tools if manager is provided
    if mock_mcp_manager and mock_mcp_manager.total_tools > 0:
        mcp_tool_defs = await mock_mcp_manager.list_all_tools()
        for t_def in mcp_tool_defs:
            tools.append(create_mcp_tool(t_def, mock_mcp_manager))

    checkpointer = MemorySaver()

    graph = build_harness_graph(
        model=chat_model,
        tools=tools,
        soul=test_soul,
        checkpointer=checkpointer,
    )

    return graph, checkpointer


# =============================================================================
# Test: Shell Execution Integration
# =============================================================================


class TestShellIntegration:
    """Tests for shell command execution through the full LangGraph pipeline."""

    @pytest.mark.asyncio
    async def test_shell_execution_safe_command(
        self,
        mock_llm_provider: MagicMock,
        skill_registry: SkillRegistry,
        test_soul: Soul,
    ) -> None:
        """
        Test that safe shell commands (auto-approved) execute without confirmation.

        Flow:
        1. LLM returns tool call for 'shell' with task 'echo hello'
        2. ShellSkill executes via Sandbox (auto-approved)
        3. LLM receives tool result and returns final response
        """
        # Configure LLM mock responses
        mock_llm_provider.complete.side_effect = [
            # First call: LLM decides to use shell skill
            LLMResponse(
                content="Let me run that command for you.",
                tool_calls=[
                    ToolCall(
                        id="tc_shell_1",
                        name="shell",
                        arguments={"task": "echo hello"},
                    )
                ],
            ),
            # Second call: LLM processes tool result and gives final answer
            LLMResponse(
                content="The command executed successfully. Output: hello",
                tool_calls=[],
            ),
        ]

        graph, _ = await create_test_graph(mock_llm_provider, skill_registry, test_soul)

        config = {"configurable": {"thread_id": "test_shell_safe_1"}}
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="Run echo hello")], "user_id": 1},
            config=config,
        )

        # Verify final response was set
        assert result.get("final_response") is not None
        assert (
            "hello" in result["final_response"].lower()
            or "successfully" in result["final_response"].lower()
        )

        # Verify LLM was called twice (tool call + final response)
        assert mock_llm_provider.complete.call_count == 2

        # Verify no interrupt occurred (no __interrupt__ key)
        assert "__interrupt__" not in result

    @pytest.mark.asyncio
    async def test_shell_execution_requires_confirmation_approved(
        self,
        mock_llm_provider: MagicMock,
        skill_registry: SkillRegistry,
        test_soul: Soul,
    ) -> None:
        """
        Test that dangerous commands trigger interrupt and can be approved.

        Flow:
        1. LLM returns tool call with requires_confirmation=True
        2. Graph pauses at sandbox_approval node (interrupt)
        3. Resume with approval
        4. Tool executes and LLM returns final response
        """
        # Configure LLM mock responses
        mock_llm_provider.complete.side_effect = [
            # First call: LLM decides to use dangerous command
            LLMResponse(
                content="I'll remove that file for you.",
                tool_calls=[
                    ToolCall(
                        id="tc_shell_confirm",
                        name="shell",
                        arguments={
                            "task": "rm /tmp/test_file.txt",
                            "requires_confirmation": True,
                        },
                    )
                ],
            ),
            # After approval: LLM processes result
            LLMResponse(
                content="The file has been removed.",
                tool_calls=[],
            ),
        ]

        graph, _ = await create_test_graph(mock_llm_provider, skill_registry, test_soul)
        config = {"configurable": {"thread_id": "test_shell_confirm_1"}}

        # First invocation - should hit interrupt
        result = await graph.ainvoke(
            {
                "messages": [HumanMessage(content="Delete /tmp/test_file.txt")],
                "user_id": 1,
            },
            config=config,
        )

        # In LangGraph 0.2.x, interrupts are in state.tasks, not in result
        # Use aget_state to check for pending interrupts
        state = await graph.aget_state(config)

        # Verify interrupt occurred - state.next should point to sandbox_approval
        # and tasks should have interrupts
        assert state.next, "Expected pending next node"
        has_interrupt = any(task.interrupts for task in state.tasks if task.interrupts)
        assert has_interrupt, f"Expected interrupt in tasks, got: {state.tasks}"

        # Get the interrupt value
        interrupt_task = next(t for t in state.tasks if t.interrupts)
        interrupt_value = interrupt_task.interrupts[0].value
        assert "question" in interrupt_value or "action" in interrupt_value

        # Resume with approval
        result = await graph.ainvoke(
            Command(resume={"approved": True}),
            config=config,
        )

        # After approval, the graph should complete
        # Note: The actual rm command may fail (file doesn't exist),
        # but the flow should complete
        assert result.get("final_response") is not None or result.get("messages")

    @pytest.mark.asyncio
    async def test_shell_execution_requires_confirmation_rejected(
        self,
        mock_llm_provider: MagicMock,
        skill_registry: SkillRegistry,
        test_soul: Soul,
    ) -> None:
        """
        Test that rejected confirmations properly cancel the operation.

        Flow:
        1. LLM returns tool call with requires_confirmation=True
        2. Graph pauses at sandbox_approval node
        3. Resume with rejection
        4. Tool is not executed, cancellation message returned
        """
        # Configure LLM mock responses
        mock_llm_provider.complete.side_effect = [
            # First call: LLM decides to use dangerous command
            LLMResponse(
                content="I'll remove that file.",
                tool_calls=[
                    ToolCall(
                        id="tc_shell_reject",
                        name="shell",
                        arguments={
                            "task": "rm -rf /tmp/important",
                            "requires_confirmation": True,
                        },
                    )
                ],
            ),
            # After rejection: LLM acknowledges cancellation
            LLMResponse(
                content="Operation cancelled by user.",
                tool_calls=[],
            ),
        ]

        graph, _ = await create_test_graph(mock_llm_provider, skill_registry, test_soul)
        config = {"configurable": {"thread_id": "test_shell_reject_1"}}

        # First invocation - should hit interrupt
        result = await graph.ainvoke(
            {
                "messages": [HumanMessage(content="Delete important folder")],
                "user_id": 1,
            },
            config=config,
        )

        # In LangGraph 0.2.x, verify interrupt via state
        state = await graph.aget_state(config)
        has_interrupt = any(task.interrupts for task in state.tasks if task.interrupts)
        assert has_interrupt, "Expected interrupt"

        # Resume with rejection
        result = await graph.ainvoke(
            Command(resume={"approved": False}),
            config=config,
        )

        # Verify cancellation was processed
        messages = result.get("messages", [])
        # Should have a tool message indicating cancellation
        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
        assert any(
            "cancelad" in m.content.lower() or "segurança" in m.content.lower()
            for m in tool_messages
        )

    @pytest.mark.asyncio
    async def test_shell_blocked_command(
        self,
        mock_llm_provider: MagicMock,
        skill_registry: SkillRegistry,
        test_soul: Soul,
    ) -> None:
        """
        Test that extremely dangerous commands are blocked entirely.

        Commands like 'rm -rf /' are blocked by the Sandbox regardless
        of user approval.
        """
        # Configure LLM to try executing a blocked command
        mock_llm_provider.complete.side_effect = [
            LLMResponse(
                content="Executing the command...",
                tool_calls=[
                    ToolCall(
                        id="tc_shell_blocked",
                        name="shell",
                        arguments={"task": "rm -rf /"},
                    )
                ],
            ),
            # LLM should receive error and respond appropriately
            LLMResponse(
                content="That command is blocked for safety reasons.",
                tool_calls=[],
            ),
        ]

        graph, _ = await create_test_graph(mock_llm_provider, skill_registry, test_soul)
        config = {"configurable": {"thread_id": "test_shell_blocked_1"}}

        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="Delete everything")], "user_id": 1},
            config=config,
        )

        # The command should be blocked - check messages for error
        messages = result.get("messages", [])
        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]

        # Should have a tool message indicating the command was blocked
        assert any(
            "bloqueado" in m.content.lower()
            or "blocked" in m.content.lower()
            or "segurança" in m.content.lower()
            for m in tool_messages
        ), f"Expected blocked message, got: {[m.content for m in tool_messages]}"


# =============================================================================
# Test: Sub-Agent Spawning Integration
# =============================================================================


class TestAgentSpawningIntegration:
    """Tests for sub-agent spawning via AgentFactory."""

    @pytest.mark.asyncio
    async def test_spawn_agent_executes_successfully(
        self,
        mock_llm_provider: MagicMock,
        skill_registry: SkillRegistry,
        test_soul: Soul,
    ) -> None:
        """
        Test that the primary agent can spawn and execute a sub-agent.

        Flow:
        1. Primary agent LLM returns tool call for 'spawn_agent'
        2. AgentFactory creates sub-agent with specified goal
        3. Sub-agent executes and returns result
        4. Primary agent incorporates result in final response
        """
        # Track call count to differentiate primary vs sub-agent calls
        call_count = 0

        async def mock_complete(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                # Primary agent: decide to spawn sub-agent
                return LLMResponse(
                    content="I'll spawn a research agent for this task.",
                    tool_calls=[
                        ToolCall(
                            id="tc_spawn_1",
                            name="spawn_agent",
                            arguments={
                                "goal": "List files in current directory",
                                "skills": ["shell"],
                            },
                        )
                    ],
                )
            elif call_count == 2:
                # Sub-agent: execute the task
                return LLMResponse(
                    content="I'll list the files.",
                    tool_calls=[
                        ToolCall(
                            id="tc_subagent_shell",
                            name="shell",
                            arguments={"task": "ls -la"},
                        )
                    ],
                )
            elif call_count == 3:
                # Sub-agent: return result
                return LLMResponse(
                    content="Files listed successfully. Found: file1.txt, file2.py",
                    tool_calls=[],
                )
            else:
                # Primary agent: final response
                return LLMResponse(
                    content="The sub-agent completed the task. It found file1.txt and file2.py.",
                    tool_calls=[],
                )

        mock_llm_provider.complete = AsyncMock(side_effect=mock_complete)

        # Create factory with the mock LLM
        factory = AgentFactory(
            llm=mock_llm_provider,
            skills=skill_registry,
            mcp=None,
            soul=test_soul,
        )

        graph, _ = await create_test_graph(
            mock_llm_provider,
            skill_registry,
            test_soul,
            factory=factory,
        )

        config = {"configurable": {"thread_id": "test_spawn_1"}}
        result = await graph.ainvoke(
            {
                "messages": [HumanMessage(content="Use a sub-agent to list files")],
                "user_id": 1,
            },
            config=config,
        )

        # Verify the spawn_agent tool was called
        assert call_count >= 2, "Expected at least 2 LLM calls (spawn + sub-agent)"

        # Verify we got a final response
        final_response = result.get("final_response")
        assert final_response is not None or len(result.get("messages", [])) > 0

        # Check that the sub-agent result is in the messages
        messages = result.get("messages", [])
        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]

        # At least one tool message should contain sub-agent result
        assert len(tool_messages) > 0, (
            "Expected tool messages from spawn_agent execution"
        )


# =============================================================================
# Test: MCP Tools Integration
# =============================================================================


class TestMCPToolsIntegration:
    """Tests for MCP tool execution via mocked MCPManager."""

    @pytest.mark.asyncio
    async def test_mcp_tool_execution(
        self,
        mock_llm_provider: MagicMock,
        skill_registry: SkillRegistry,
        test_soul: Soul,
        mock_mcp_manager: MagicMock,
    ) -> None:
        """
        Test that MCP tools are correctly invoked through the graph.

        Flow:
        1. LLM returns tool call for 'read_file' (MCP tool)
        2. MCPManager.call_tool is invoked with correct arguments
        3. Result is passed back to LLM
        4. LLM returns final response
        """
        mock_llm_provider.complete.side_effect = [
            # First call: LLM decides to use MCP tool
            LLMResponse(
                content="I'll read that file for you.",
                tool_calls=[
                    ToolCall(
                        id="tc_mcp_1",
                        name="read_file",
                        arguments={"path": "/etc/hosts"},
                    )
                ],
            ),
            # Second call: LLM processes result
            LLMResponse(
                content="Here are the file contents: Hello from MCP!",
                tool_calls=[],
            ),
        ]

        graph, _ = await create_test_graph(
            mock_llm_provider,
            skill_registry,
            test_soul,
            mock_mcp_manager=mock_mcp_manager,
        )

        config = {"configurable": {"thread_id": "test_mcp_1"}}
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="Read /etc/hosts")], "user_id": 1},
            config=config,
        )

        # Verify MCPManager.call_tool was called
        mock_mcp_manager.call_tool.assert_called_once()
        call_args = mock_mcp_manager.call_tool.call_args
        assert call_args[0][0] == "read_file"
        assert call_args[0][1]["path"] == "/etc/hosts"

        # Verify final response contains MCP result
        final_response = result.get("final_response")
        assert final_response is not None
        assert "mcp" in final_response.lower() or "file" in final_response.lower()

    @pytest.mark.asyncio
    async def test_mcp_tool_handles_error(
        self,
        mock_llm_provider: MagicMock,
        skill_registry: SkillRegistry,
        test_soul: Soul,
        mock_mcp_manager: MagicMock,
    ) -> None:
        """
        Test that MCP tool errors are handled gracefully.

        Flow:
        1. LLM returns tool call for MCP tool
        2. MCPManager.call_tool returns error result
        3. Error is passed to LLM
        4. LLM responds with error message
        """
        # Configure MCP manager to return error
        mock_mcp_manager.call_tool = AsyncMock(
            return_value=MCPToolResult(
                content="Error: File not found - /nonexistent/file.txt",
                is_error=True,
            )
        )

        mock_llm_provider.complete.side_effect = [
            # First call: LLM tries to read file
            LLMResponse(
                content="I'll read that file.",
                tool_calls=[
                    ToolCall(
                        id="tc_mcp_error",
                        name="read_file",
                        arguments={"path": "/nonexistent/file.txt"},
                    )
                ],
            ),
            # Second call: LLM handles error
            LLMResponse(
                content="I couldn't read the file. Error: File not found.",
                tool_calls=[],
            ),
        ]

        graph, _ = await create_test_graph(
            mock_llm_provider,
            skill_registry,
            test_soul,
            mock_mcp_manager=mock_mcp_manager,
        )

        config = {"configurable": {"thread_id": "test_mcp_error_1"}}
        result = await graph.ainvoke(
            {
                "messages": [HumanMessage(content="Read /nonexistent/file.txt")],
                "user_id": 1,
            },
            config=config,
        )

        # Verify error was processed
        messages = result.get("messages", [])
        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]

        # Tool message should contain error
        assert any(
            "erro" in m.content.lower()
            or "error" in m.content.lower()
            or "not found" in m.content.lower()
            for m in tool_messages
        ), f"Expected error in tool messages: {[m.content for m in tool_messages]}"

        # Final response should acknowledge the error
        final_response = result.get("final_response")
        assert final_response is not None
        assert "error" in final_response.lower() or "couldn't" in final_response.lower()
