"""Tests for the PrimaryAgent (main orchestrator)."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from harness.agents.primary import PrimaryAgent
from harness.channels.base import IncomingMessage
from harness.providers.llm_provider import LLMResponse, ToolCall
from harness.skills.base import BaseSkill, SkillResult, SkillContext
from harness.skills.registry import SkillRegistry
from harness.soul.loader import Soul


@pytest.fixture
def mock_soul() -> Soul:
    """Create a mock soul."""
    return Soul(
        name="TestBot",
        mood="professional",
        tone="Helpful and direct.",
        require_confirmation_patterns=["rm -rf*"],
        auto_approve_patterns=["ls*", "echo*"],
    )


@pytest.fixture
def mock_llm() -> MagicMock:
    """Create a mock LLM provider."""
    llm = MagicMock()
    llm.complete = AsyncMock()
    return llm


@pytest.fixture
def mock_memory() -> MagicMock:
    """Create a mock memory repository."""
    memory = MagicMock()
    memory.get_history = AsyncMock(return_value=[])
    memory.append_message = AsyncMock()
    return memory


@pytest.fixture
def mock_skills() -> SkillRegistry:
    """Create a skill registry with a test skill."""
    class TestSkill(BaseSkill):
        name = "test_skill"
        description = "A test skill"
        
        async def execute(self, task: str, context: SkillContext) -> SkillResult:
            return SkillResult(content=f"Test executed: {task}", skill_name=self.name)
    
    registry = SkillRegistry()
    registry.register(TestSkill())
    return registry


@pytest.fixture
def primary_agent(
    mock_llm: MagicMock,
    mock_soul: Soul,
    mock_skills: SkillRegistry,
    mock_memory: MagicMock,
) -> PrimaryAgent:
    """Create a PrimaryAgent with mocked dependencies."""
    return PrimaryAgent(
        llm_provider=mock_llm,
        soul=mock_soul,
        skills=mock_skills,
        memory=mock_memory,
        mcp_manager=None,
    )


@pytest.fixture
def incoming_message() -> IncomingMessage:
    """Create a test incoming message."""
    return IncomingMessage(
        user_id=12345,
        username="testuser",
        text="Hello, how are you?",
        channel="telegram",
        timestamp=datetime.now(tz=timezone.utc),
        raw={"message_id": 1, "chat_id": 12345},
    )


class TestPrimaryAgentProcess:
    """Tests for message processing."""
    
    @pytest.mark.asyncio
    async def test_simple_response_no_tools(
        self,
        primary_agent: PrimaryAgent,
        incoming_message: IncomingMessage,
        mock_llm: MagicMock,
    ) -> None:
        """Process a simple message that doesn't require tools."""
        mock_llm.complete.return_value = LLMResponse(
            content="I'm doing well, thank you!",
            tool_calls=[],
        )
        
        response = await primary_agent.process(incoming_message)
        
        assert response == "I'm doing well, thank you!"
        mock_llm.complete.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_response_with_tool_call(
        self,
        primary_agent: PrimaryAgent,
        incoming_message: IncomingMessage,
        mock_llm: MagicMock,
    ) -> None:
        """Process a message that triggers a tool call."""
        # First response has tool call
        mock_llm.complete.side_effect = [
            LLMResponse(
                content="Let me check that for you.",
                tool_calls=[
                    ToolCall(
                        id="call_123",
                        name="test_skill",
                        arguments={"task": "run test"},
                    )
                ],
            ),
            # Second response after tool execution
            LLMResponse(
                content="Here's the result: Test executed: run test",
                tool_calls=[],
            ),
        ]
        
        response = await primary_agent.process(incoming_message)
        
        assert "Test executed" in response or "result" in response
        assert mock_llm.complete.call_count == 2
    
    @pytest.mark.asyncio
    async def test_history_loaded(
        self,
        primary_agent: PrimaryAgent,
        incoming_message: IncomingMessage,
        mock_llm: MagicMock,
        mock_memory: MagicMock,
    ) -> None:
        """History is loaded from memory."""
        mock_memory.get_history.return_value = [
            {"role": "user", "content": "Previous message"},
            {"role": "assistant", "content": "Previous response"},
        ]
        mock_llm.complete.return_value = LLMResponse(content="Current response", tool_calls=[])
        
        await primary_agent.process(incoming_message)
        
        mock_memory.get_history.assert_called_once_with(incoming_message.user_id, limit=20)
    
    @pytest.mark.asyncio
    async def test_conversation_persisted(
        self,
        primary_agent: PrimaryAgent,
        incoming_message: IncomingMessage,
        mock_llm: MagicMock,
        mock_memory: MagicMock,
    ) -> None:
        """Conversation is saved to memory."""
        mock_llm.complete.return_value = LLMResponse(content="Response", tool_calls=[])
        
        await primary_agent.process(incoming_message)
        
        # Should save both user and assistant messages
        assert mock_memory.append_message.call_count == 2


class TestPrimaryAgentMultimodal:
    """Tests for multimodal message handling."""
    
    @pytest.mark.asyncio
    async def test_multimodal_message_format(
        self,
        primary_agent: PrimaryAgent,
        mock_llm: MagicMock,
    ) -> None:
        """Multimodal messages are formatted correctly."""
        multimodal_message = IncomingMessage(
            user_id=12345,
            username="testuser",
            text="What's in this image?",
            channel="telegram",
            timestamp=datetime.now(tz=timezone.utc),
            raw={
                "message_id": 1,
                "chat_id": 12345,
                "images": ["data:image/jpeg;base64,/9j/4AAQ..."],
            },
        )
        
        mock_llm.complete.return_value = LLMResponse(
            content="I see a cat in the image.",
            tool_calls=[],
        )
        
        await primary_agent.process(multimodal_message)
        
        # Check that the call includes image content
        call_args = mock_llm.complete.call_args
        messages = call_args.kwargs.get("messages") or call_args[0][0]
        
        # Find the user message
        user_msg = next((m for m in messages if m["role"] == "user"), None)
        assert user_msg is not None
        
        # Should have multimodal content
        content = user_msg["content"]
        assert isinstance(content, list)
        assert any(c.get("type") == "text" for c in content)
        assert any(c.get("type") == "image_url" for c in content)


class TestPrimaryAgentTools:
    """Tests for tool handling."""
    
    @pytest.mark.asyncio
    async def test_get_all_tools_includes_skills(
        self,
        primary_agent: PrimaryAgent,
        mock_skills: SkillRegistry,
    ) -> None:
        """All tools list includes registered skills."""
        tools = await primary_agent._get_all_tools()
        
        # Should include the test_skill
        skill_names = [t["function"]["name"] for t in tools]
        assert "test_skill" in skill_names
    
    @pytest.mark.asyncio
    async def test_execute_skill_tool(
        self,
        primary_agent: PrimaryAgent,
    ) -> None:
        """Execute a skill via tool call."""
        tool_call = ToolCall(
            id="call_abc",
            name="test_skill",
            arguments={"task": "do something"},
        )
        
        result = await primary_agent._execute_single_tool(tool_call, user_id=12345)
        
        assert "Test executed: do something" in result
    
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(
        self,
        primary_agent: PrimaryAgent,
    ) -> None:
        """Unknown tools return an error message."""
        tool_call = ToolCall(
            id="call_xyz",
            name="nonexistent_tool",
            arguments={},
        )
        
        result = await primary_agent._execute_single_tool(tool_call, user_id=12345)
        
        assert "não encontrada" in result.lower() or "not found" in result.lower()


class TestPrimaryAgentFactory:
    """Tests for agent factory integration."""
    
    def test_set_factory(
        self,
        primary_agent: PrimaryAgent,
    ) -> None:
        """Factory can be set after construction."""
        mock_factory = MagicMock()
        
        primary_agent.set_factory(mock_factory)
        
        assert primary_agent._factory is mock_factory
    
    @pytest.mark.asyncio
    async def test_spawn_agent_tool_available_when_factory_set(
        self,
        primary_agent: PrimaryAgent,
    ) -> None:
        """spawn_agent tool is available when factory is set."""
        mock_factory = MagicMock()
        mock_factory.as_tool_definition.return_value = {
            "type": "function",
            "function": {
                "name": "spawn_agent",
                "description": "Spawn a sub-agent",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        primary_agent.set_factory(mock_factory)
        
        tools = await primary_agent._get_all_tools()
        
        tool_names = [t["function"]["name"] for t in tools]
        assert "spawn_agent" in tool_names


class TestPrimaryAgentErrorHandling:
    """Tests for error handling."""
    
    @pytest.mark.asyncio
    async def test_llm_error_returns_friendly_message(
        self,
        primary_agent: PrimaryAgent,
        incoming_message: IncomingMessage,
        mock_llm: MagicMock,
    ) -> None:
        """LLM errors result in a friendly error message."""
        from harness.providers.llm_provider import LLMProviderError
        
        mock_llm.complete.side_effect = LLMProviderError("Connection failed")
        
        response = await primary_agent.process(incoming_message)
        
        assert "indisponível" in response.lower() or "tente novamente" in response.lower()
    
    @pytest.mark.asyncio
    async def test_empty_response_handled(
        self,
        primary_agent: PrimaryAgent,
        incoming_message: IncomingMessage,
        mock_llm: MagicMock,
    ) -> None:
        """Empty LLM response is handled gracefully."""
        mock_llm.complete.return_value = LLMResponse(content=None, tool_calls=[])
        
        response = await primary_agent.process(incoming_message)
        
        # Should return a fallback message
        assert response == "(sem resposta)"
