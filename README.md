# nar

`nar` is a CLI coding agent written in Python. This version provides the command-line
skeleton, the configuration system, `AGENTS.md` loading and the LLM providers (Anthropic,
OpenRouter, Ollama); the tools and the agent loop arrive in later changes (see the specs in
[nar-specs](https://github.com/rojb/nar-specs)).

## Requirements

- Python 3.11 or newer
- `pip` (no other tool is required)

## Install

```bash
git clone https://github.com/rojb/nar
cd nar
python -m venv .venv
```

Activate the virtual environment:

```bash
# Windows PowerShell
.venv\Scripts\Activate.ps1

# Windows Git Bash / Linux / macOS
source .venv/Scripts/activate   # Windows Git Bash
source .venv/bin/activate       # Linux / macOS
```

Then install the package in editable mode with the development tools:

```bash
pip install -e ".[dev]"
```

## Usage

```bash
nar                      # interactive mode (placeholder REPL; type "exit" to leave)
nar "explain this repo"  # single mode: streams the model's answer (no tools yet)
nar --provider ollama --model qwen2.5-coder "hello"   # choose provider/model for this run
nar --version
nar --help
nar --show-config        # print the effective configuration and where each value comes from
```

In interactive mode, empty lines are ignored; `exit`, end-of-input (Ctrl+D, or Ctrl+Z then Enter
on Windows) and Ctrl+C end the session with exit code 0.

## Configuration

`nar` reads two optional TOML files and merges them; project values override user values, and
nested tables (such as `mcp_servers`) are merged key by key:

| File | Scope |
|---|---|
| `~/.nar/config.toml` | user (all projects) |
| `.nar/config.toml` | project (current working directory) |

| Key | Values | Default |
|---|---|---|
| `provider` | `anthropic`, `openrouter`, `ollama` | `openrouter` |
| `model` | any model name of that provider | the provider's default model (see below) |
| `approval` | `ask`, `auto-edit`, `yolo` | `ask` |
| `mcp_servers` | table of named server definitions (reserved for RRG-74) | empty |

Example `.nar/config.toml`:

```toml
model = "openrouter/auto"
approval = "auto-edit"
```

Unknown keys, invalid values and malformed TOML stop `nar` with a one-line error and exit
code 1. `nar --show-config` prints each effective key with its source (`default`, the user
file, the project file or `command line`).

`--provider` and `--model` override the files for one run. A `model` set in a file is kept
when only `--provider` is given, so pass both flags when switching to a provider that does not
know that model.

## Providers

| Provider | Default model | Needs |
|---|---|---|
| `anthropic` | `claude-sonnet-5-5` | `ANTHROPIC_API_KEY` |
| `openrouter` | `openrouter/auto` | `OPENROUTER_API_KEY` |
| `ollama` | `qwen2.5-coder` | a running Ollama server (`ollama serve`) with the model pulled |

API keys are read only from environment variables, never from the config files, so they cannot
be committed by accident:

```bash
# Linux / macOS / Git Bash
export OPENROUTER_API_KEY="sk-or-..."
# Windows PowerShell
$env:OPENROUTER_API_KEY = "sk-or-..."
```

Rate limits (429), server errors (5xx), timeouts and connection errors are retried up to 3 times
with exponential backoff (about 1 s, 2 s, 4 s, or the wait the provider asks for, at most 60 s);
each retry is announced on stderr. A bad API key or an unknown model fails at once with a
one-line error.

To check a provider without the agent loop, `scripts/run_provider.py` sends one prompt and
prints the text plus the internal response (tool calls, stop reason, token usage):

```bash
python scripts/run_provider.py --provider ollama --model qwen2.5-coder "Say hello"
python scripts/run_provider.py --provider ollama --model qwen2.5-coder --with-tool --no-stream "What is the weather in La Paz?"
```

`--with-tool` offers a sample `get_weather` tool; `--no-stream` uses `complete()` instead of
`stream()`.

## Environment variables

| Variable | Effect |
|---|---|
| `NAR_HOME` | Use this folder instead of `~/.nar` (handy for trying things without touching your real home). |
| `NAR_DEBUG=1` | Print the full traceback in addition to the error message. |
| `ANTHROPIC_API_KEY` | API key for the `anthropic` provider. |
| `OPENROUTER_API_KEY` | API key for the `openrouter` provider. |
| `OLLAMA_HOST` | Ollama server address (default `http://localhost:11434`; scheme and port are optional). |

## AGENTS.md

At startup `nar` looks for an `AGENTS.md` in the current directory, then in each parent up to
and including the git repository root. The first one found is loaded as project instructions
(at most 64 KB; larger files are truncated with a warning, unreadable or non-UTF-8 files are
skipped with a warning). Interactive mode shows `Using AGENTS.md: <path>` when one is in use.

## Development

```bash
ruff check .
ruff format --check .
pytest
```
