# nar

`nar` is a CLI coding agent written in Python. It talks to a model (Anthropic, OpenRouter, DeepSeek or a
local Ollama), lets it call tools (for now `read_file`) in a loop until it has an answer, and keeps
the conversation in interactive mode. More tools, approvals, compaction and sessions arrive in
later changes (see the specs in [nar-specs](https://github.com/rojb/nar-specs)).

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
nar                      # interactive mode: one conversation, type "exit" to leave
nar "explain this repo"  # single mode: runs the agent on one prompt and exits
nar --provider ollama --model qwen2.5-coder "hello"   # choose provider/model for this run
nar --version
nar --help
nar --show-config        # print the effective configuration and where each value comes from
```

In interactive mode, empty lines are ignored; `exit`, end-of-input (Ctrl+D, or Ctrl+Z then Enter
on Windows) and Ctrl+C at the prompt end the session with exit code 0.

## How the agent works

For each request `nar` sends the conversation and the tool definitions to the model. When the
model asks for tools, `nar` runs them (in the order requested), sends the results back and asks
again, until the model answers without tools or `max_turns` model calls were made.

- **Output:** the answer is streamed to standard output; tool calls (`-> read_file(...)`), their
  results, the "waiting for the model" spinner and warnings go to standard error. So
  `nar "..." > answer.txt` saves only the answer.
- **Interactive mode** keeps the whole conversation, so follow-up questions can refer to earlier
  answers and tool results.
- **Ctrl+C** while the agent is working cancels that request (it is removed from the
  conversation) and returns to the prompt; in single mode it exits with code 130.
- **Turn limit:** when `max_turns` is reached `nar` stops with a message; single mode then exits
  with code 1.
- **Provider errors** in interactive mode are shown on one line and the session continues.
- **Tools that need approval** (none of the current built-in tools) only run with
  `approval = "yolo"` until the approval prompts of RRG-69 exist; otherwise the model is told it
  cannot use them.
- **System prompt:** it tells the model it is `nar`, how to use its tools, the working
  directory, OS and date, and adds the project's `AGENTS.md` when one is found.

## Configuration

`nar` reads two optional TOML files and merges them; project values override user values, and
nested tables (such as `mcp_servers`) are merged key by key:

| File | Scope |
|---|---|
| `~/.nar/config.toml` | user (all projects) |
| `.nar/config.toml` | project (current working directory) |

| Key | Values | Default |
|---|---|---|
| `provider` | `anthropic`, `openrouter`, `ollama`, `deepseek` | `openrouter` |
| `model` | any model name of that provider | the provider's default model (see below) |
| `approval` | `ask`, `auto-edit`, `yolo` | `ask` |
| `max_turns` | whole number, at least 1: model calls allowed per request | `20` |
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
| `deepseek` | `deepseek-flash` | `DEEPSEEK_API_KEY` |

API keys are read only from environment variables, never from the config files, so they cannot
be committed by accident:

```bash
# Linux / macOS / Git Bash
export OPENROUTER_API_KEY="sk-or-..."
export DEEPSEEK_API_KEY="sk-..."
# Windows PowerShell
$env:OPENROUTER_API_KEY = "sk-or-..."
$env:DEEPSEEK_API_KEY = "sk-..."
```

DeepSeek is called at `https://api.deepseek.com`. `nar` always turns DeepSeek's thinking mode
off (`thinking: {"type": "disabled"}`), because its tool loop cannot send `reasoning_content`
back to the API. The default model `deepseek-flash` comes from the DeepSeek docs read on
2026-10-27; `deepseek-v4-pro` is also available with `--model`. An empty balance fails with a
one-line `insufficient balance` error.

Rate limits (429), server errors (5xx), timeouts and connection errors are retried up to 3 times
with exponential backoff (about 1 s, 2 s, 4 s, or the wait the provider asks for, at most 60 s);
each retry is announced on stderr. A bad API key or an unknown model fails at once with a
one-line error.

To check a provider without the agent loop, `scripts/run_provider.py` sends one prompt and
prints the text plus the internal response (tool calls, stop reason, token usage):

```bash
python scripts/run_provider.py --provider ollama --model qwen2.5-coder "Say hello"
python scripts/run_provider.py --provider ollama --model qwen2.5-coder --with-tool --no-stream "What is the weather in La Paz?"
python scripts/run_provider.py --provider deepseek --with-tool --no-stream "What is the weather in La Paz?"
```

`--with-tool` offers a sample `get_weather` tool; `--no-stream` uses `complete()` instead of
`stream()`.

To run a built-in tool without an LLM, `scripts/run_tool.py` takes the tool name and its
arguments as a JSON object (default `{}`), prints the call and a result line, then the output the
model would see. Relative paths resolve against the current folder. Exit code `0` means success,
`1` means the tool returned an error (unknown tool, bad arguments, file not found), and `2` means
the arguments are not a valid JSON object. On Windows use Git Bash or PowerShell 7.3+, because
Windows PowerShell 5.1 strips the inner double quotes:

```bash
python scripts/run_tool.py read_file '{"path": "pyproject.toml", "limit": 5}'
```

### Built-in tools

| Tool | What it does | Approval |
|---|---|---|
| `read_file` | Read a text file with numbered lines. | no |
| `write_file` | Create a file or replace it entirely; missing folders are created. | yes |
| `edit_file` | Replace one exact piece of text; shows a diff. | yes |
| `list_dir` | List one folder (not recursive); hides `.git`, `node_modules`, `.venv`, caches. | no |
| `glob` | Find files by path pattern (`**/*.py`); at most 200 paths. | no |
| `grep` | Search file contents with a regular expression; `path:line:text`, at most 100 matches. | no |
| `web_search` | Search the web with DuckDuckGo (no API key); title, URL and snippet, 5 results by default. | no |
| `web_fetch` | Download an http/https page and return it as Markdown (or text/JSON). | no |

`write_file` and `edit_file` are marked as requiring approval, but nothing asks yet: the
approval prompt arrives with RRG-69. Until then the agent runs them only with
`approval = "yolo"` (see "How the agent works"); `scripts/run_tool.py` always runs them.

`edit_file` fails unless `old_string` matches exactly once, so include surrounding lines to make
it unique. Line breaks written as `\n` match both LF and CRLF files; the new text uses the file's
own line-break style (CRLF only when the file has more CRLF than LF) and every other byte of the
file stays as it was. `write_file` writes the content as UTF-8 with no newline conversion.

Create a file in a scratch folder, then edit it (the second command prints a colored diff):

```bash
T=$(mktemp -d)
python scripts/run_tool.py write_file "{\"path\": \"$T/notes.txt\", \"content\": \"timeout = 10\nretries = 3\n\"}"
python scripts/run_tool.py edit_file "{\"path\": \"$T/notes.txt\", \"old_string\": \"timeout = 10\", \"new_string\": \"timeout = 30\"}"
```

`glob` and `grep` search the working directory (or `path`) and skip what git ignores
(`.gitignore` files at any level, `.git/info/exclude` and your global excludes) by asking
`git ls-files`; outside a git repository, or without git installed, they only skip the folders
`list_dir` hides. In `glob` patterns `*` and `?` stay inside one folder, `**` spans folders, and
a pattern without `/` matches file names at any depth (`test_*.py` finds `tests/unit/test_a.py`).
`grep` uses Python regular expressions, can be limited with `glob` (for example `"*.py"`) and
`ignore_case`, skips binary files and files over 10 MB, and cuts shown lines to 300 characters.

```bash
python scripts/run_tool.py glob '{"pattern": "**/*.py", "path": "src"}'
python scripts/run_tool.py grep '{"pattern": "def load_config", "glob": "*.py"}'
```

`web_search` sends the query to DuckDuckGo's HTML page, so it needs no account or key; if
DuckDuckGo rate-limits you the tool says so and the agent can try later. `web_fetch` follows
redirects, gives up after 20 seconds and stops downloading after 5 MB; HTML pages come back as
Markdown with absolute links and without scripts, menus or footers, and images, PDFs and other
binary files are refused. Both only read, so they need no approval; every URL they use is shown
in the tool activity line.

```bash
python scripts/run_tool.py web_search '{"query": "httpx python documentation", "max_results": 3}'
python scripts/run_tool.py web_fetch '{"url": "https://www.python-httpx.org/"}'
```

## Environment variables

| Variable | Effect |
|---|---|
| `NAR_HOME` | Use this folder instead of `~/.nar` (handy for trying things without touching your real home). |
| `NAR_DEBUG=1` | Print the full traceback in addition to the error message. |
| `ANTHROPIC_API_KEY` | API key for the `anthropic` provider. |
| `OPENROUTER_API_KEY` | API key for the `openrouter` provider. |
| `DEEPSEEK_API_KEY` | API key for the `deepseek` provider. |
| `OLLAMA_HOST` | Ollama server address (default `http://localhost:11434`; scheme and port are optional). |

## AGENTS.md

At startup `nar` looks for an `AGENTS.md` in the current directory, then in each parent up to
and including the git repository root. The first one found is loaded as project instructions
(at most 64 KB; larger files are truncated with a warning, unreadable or non-UTF-8 files are
skipped with a warning). Interactive mode shows `Using AGENTS.md: <path>` when one is in use,
and its content is added to the agent's system prompt.

## Development

```bash
ruff check .
ruff format --check .
pytest
```
