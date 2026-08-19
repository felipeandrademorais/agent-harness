# Agent Harness

Multi-agent AI system built on **LangGraph StateGraph** with personality (**Soul**), reusable capabilities (**Skills**), **MCP Tools**, and dynamic sub-agent spawning. Receives messages via **Telegram**, processes them through a resilient LangGraph agentic loop with human-in-the-loop sandbox protection, and persists conversation state in **PostgreSQL**.

---

## 🏛️ Architecture

```
User (Telegram)
  → TelegramChannel  (aiogram 3.x, whitelist middleware, multimodal)
  → Dispatcher       (timeout handling, error recovery)
  → PrimaryAgent     (LangGraph StateGraph orchestrator)
      ├─→ Soul            (personality & system prompt)
      ├─→ Skills          (reusable capabilities: shell, ADO, SQL, etc.)
      ├─→ MCP Tools       (external tools via Model Context Protocol)
      ├─→ Sandbox         (Human-in-the-loop interrupt for dangerous operations)
      └─→ AgentFactory    (LangGraph sub-agents for complex tasks)
  → PostgreSQL        (State Checkpointer & conversation history)
```

> 📖 For a detailed sequence diagram and end-to-end execution flow, see [ARCHITECTURE.md](ARCHITECTURE.md).

---

### Core Concepts

| Concept | Description |
|---------|-------------|
| **LangGraph StateGraph** | Stateful agent graph orchestrating model calls, tool execution, and sandbox interrupts |
| **Soul** | Personality, tone of voice, behaviors (what requires confirmation vs auto-approved) |
| **Skills** | Reusable capabilities with defined interfaces (shell, daily_report, code_review, etc.) |
| **Sandbox** | Permission system that classifies operations and triggers LangGraph `interrupt()` for human approval |
| **Agent Spawning** | Dynamically create sub-agents with specific goals and limited skills using LangGraph subgraphs |
| **Multimodal** | Process images alongside text (via Telegram photos) |

---

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (package manager)
- Docker (for PostgreSQL)
- [Ollama](https://ollama.ai) running locally with a vision-capable model (e.g., `llava`, `llama3.2`)
- A Telegram bot token (create via [@BotFather](https://t.me/BotFather))

---

## Quick Start

```bash
# 1. Clone and install
git clone <repo>
cd agent-harness
uv sync --all-extras

# 2. Start PostgreSQL (port 5455)
docker compose up -d db

# 3. Run setup wizard
uv run ah init

# 4. Verify everything is working
uv run ah doctor

# 5. Start the bot
uv run ah start
```

---

## CLI Commands

The harness includes a full-featured CLI (`ah`) for configuration and management.

> **Note:** All commands use `uv run ah` to execute within the project's virtual environment.

### Setup & Configuration

```bash
uv run ah init                    # Interactive setup wizard
uv run ah init --telegram-token=X --db-url=Y --user-ids=123  # One-shot setup
uv run ah doctor                  # Health check — verify all components
```

### Bot Lifecycle

```bash
uv run ah start                   # Start bot (dev=foreground, prod=daemon)
uv run ah start --foreground      # Force foreground mode
uv run ah start --daemon          # Force daemon mode
uv run ah stop                    # Stop the running bot
uv run ah status                  # Show bot status
```

### Configuration Management

```bash
uv run ah config show             # Show current config (secrets redacted)
uv run ah config show --raw       # Show raw JSON
uv run ah config show -s telegram # Show only telegram section
uv run ah config set env prod     # Set a config value
uv run ah config set llm.model ollama_chat/llama3.2
uv run ah config edit             # Open config.json in $EDITOR
uv run ah config edit --soul      # Edit soul.md
uv run ah config edit --mcp       # Edit mcp.json
uv run ah config path             # Show config directory path
```

### Skills Management

```bash
uv run ah skills list             # List installed skills
uv run ah skills add /path/to/skill.py          # Add from local path
uv run ah skills add git@github.com:user/repo   # Add from git
uv run ah skills remove my_skill  # Remove a user skill
```

---

## Configuration

Configuration is stored in `~/.agent-harness/`:

```
~/.agent-harness/
├── config.json     # Main config (telegram, database, llm, daemon)
├── mcp.json        # MCP servers configuration
├── soul.md         # Personality (Markdown with YAML frontmatter)
├── skills/         # User-installed skills
├── logs/           # Log files (daemon mode)
├── data/           # Runtime data
└── pid/            # PID files
```

---

## Skills

Skills are reusable capabilities that the PrimaryAgent can invoke.

### Built-in Skills

| Skill | Description |
|-------|-------------|
| `shell` | Execute shell commands with sandbox protection |
| `daily_report` | Generate daily reports from ADO and Git |
| `code_review` | Code review with GitLab MCP integration |
| `ado` | Azure DevOps work item management |
| `sql` | SQL queries with PostgreSQL MCP integration |

---

## Sandbox & Permissions

The sandbox classifies shell commands into three categories:

| Level | Behavior | Examples |
|-------|----------|----------|
| **Allowed** | Executes immediately | `ls`, `cat`, `git status` |
| **Requires Confirmation** | Triggers `interrupt()` and asks user via Telegram | `rm -rf /tmp/test`, `docker stop` |
| **Blocked** | Never executes | `rm -rf /`, `:(){ :|:& };:` |

Configuration is in `soul.md` under `behaviors`.

---

## Project Structure

```
agent-harness/
├── ARCHITECTURE.md          # End-to-end architecture & sequence documentation
├── harness/
│   ├── agents/              # LangGraph Agent implementation
│   │   ├── state.py         # AgentState schema
│   │   ├── graph.py         # LangGraph StateGraph builder
│   │   ├── primary.py       # PrimaryAgent wrapper
│   │   ├── factory.py       # AgentFactory for sub-agents
│   │   ├── tools_adapter.py # LangChain tool converters
│   │   └── base.py          # Sub-agent dataclasses
│   ├── channels/            # Communication channels (Telegram)
│   ├── cli/                 # CLI commands (ah)
│   ├── core/                # Core utilities (Dispatcher, Sandbox)
│   ├── memory/              # Conversation storage & checkpointer
│   ├── providers/           # LLM and MCP providers
│   │   ├── chat_model.py    # LiteLLMChatModel adapter for LangGraph
│   │   └── llm_provider.py  # LiteLLM provider wrapper
│   ├── skills/              # Skill system & builtin skills
│   └── soul/                # Personality loader
├── config/                  # Configuration files
├── scripts/                 # Utility scripts
├── tests/                   # Test suite (130+ tests)
├── main.py                  # Entry point (used by ah start)
└── pyproject.toml
```

---

## Development

```bash
# Install with dev dependencies
uv sync --all-extras

# Run tests
uv run pytest

# Run a single test file
uv run pytest tests/test_langgraph_agent.py -v

# Test CLI
uv run ah --help
uv run ah doctor
```
