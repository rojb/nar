# nar

`nar` is a CLI coding agent written in Python. It talks to a model (Anthropic, OpenRouter, DeepSeek or a
local Ollama), lets it call tools (for now `read_file`) in a loop until it has an answer, and keeps
the conversation in interactive mode, reducing it when it gets too long. It asks before it edits
files or runs commands, and saves each conversation so you can continue it later. More tools arrive in later changes (see the specs in [nar-specs](https://github.com/rojb/nar-specs)).

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
nar --approval auto-edit "add a docstring to main.py"  # edit project files without asking
nar --language en "explain this repo"  # get the answer in English (Spanish is the default)
nar --resume             # continue the latest session of this folder
nar --resume 20261103 "and now?"  # continue that session with one prompt and exit
nar --trust-project-tools "use my tool"  # allow this project's tool files without asking
nar --version
nar --help
nar --show-config        # print the effective configuration and where each value comes from
```

In interactive mode, empty lines are ignored; `exit`, end-of-input (Ctrl+D, or Ctrl+Z then Enter
on Windows) and Ctrl+C at the prompt end the session with exit code 0. A line such as `/mcp`, `/compact`, `/sessions` or `/checkpoint` is a
command and is not sent to the model (see "MCP servers", "Long conversations", "Sessions" and "Checkpoints"); a path such as `/etc/hosts` is not a
command and is sent as a request.

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
- **Repeated calls:** if the model calls a tool again with exactly the same arguments three times
  in a row, or alternates between the same two calls twice without anything changing, `nar` adds a
  message asking it to change strategy and the request goes on. If the same calls repeat after that
  warning, the request stops with a message; the session continues and single mode exits with code
  1. Calls that failed count after two repetitions instead of three. The thresholds are not
  configurable.
- **Failed tools are explained:** the model receives a failing tool result inside a fixed envelope
  naming the tool, the kind of failure (unknown tool, invalid arguments, tool error, unexpected
  error) and the original message, plus a line saying that repeating the call will fail the same
  way. Results that succeed, and calls the user rejected, reach the model unchanged.
- **Provider errors** in interactive mode are shown on one line and the session continues.
- **Tools that need approval** (`write_file`, `edit_file`, `shell` and every MCP tool) ask you first, unless the
  approval level says otherwise (see "Approvals"). If you say no, or `nar` cannot ask, the model
  is told and can try something else or answer.
- **System prompt:** it tells the model it is `nar`, how to use its tools, the working
  directory, OS and date, and adds the project's `AGENTS.md` and the list of available skills
  (see "Skills") when there are any.

## Commands

In interactive mode a line that starts with `/` is a command, not a request. Commands answer on
standard error, so a redirected run keeps only the answer.

| Command | What it does |
|---|---|
| `/help` | list every command with its summary |
| `/model` | show the provider and model, or change them in place |
| `/approval` | show the approval level, or change it in place |
| `/clear` | start a new conversation, keeping the provider and the approval level |
| `/config` | show the effective configuration and the file each value came from |
| `/tools` | list the tools the model can call, including the MCP ones |
| `/tokens` | show the tokens used, the context window and the percentage |
| `/compact` | summarize the conversation to free context |
| `/sessions` | list the saved sessions |
| `/resume` | continue a saved session: `/resume [id]` |
| `/mcp` | show the MCP servers and their tools |
| `/skills` | list the available skills and where each came from |
| `/skill` | use a skill for the next request: `/skill <name> [request]` |
| `/exit` | leave, exactly like `exit` |

- **`/model` and `/approval` change the session in place.** The conversation, the tools and the
  session stay; only the next model call uses the new provider, and the next tool call uses the new
  approval level. `/model ollama`, `/model ollama/qwen2.5-coder` and `/model ollama qwen2.5-coder`
  all work; an unknown provider, or one whose API key is missing, is rejected with a one-line error
  and the previous provider stays in use. The session file keeps the provider it was created with,
  so a later `--resume` starts from the configuration again.
- **Completion needs a terminal.** When standard input is a terminal the prompt completes `/`
  commands and shows their summaries; with a pipe or a redirected file it reads a plain line and
  everything else behaves the same, so `printf '/help\nexit\n' | nar` works.
- **`/clear` says so**: the earlier messages are gone from the conversation and from the session
  file, and the next request starts from the system prompt alone.

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
| `language` | `es`, `en`: the language the model replies in (see "Reply language") | `es` |
| `max_turns` | whole number, at least 1: model calls allowed per request | `20` |
| `context_window` | whole number, at least 1000: size of the model's context in tokens | by provider (see "Long conversations") |
| `context_threshold` | whole number from 1 to 100: percentage of the window where `nar` starts reducing the conversation | `80` |
| `subagent_max_turns` | whole number, at least 1: model calls a subagent may make for one task | `15` |
| `mcp_servers` | table of named MCP server definitions, `[mcp_servers.<name>]` (see "MCP servers") | empty |
| `hooks` | table of shell commands run around the loop, `[hooks]` (see "Hooks") | empty |
| `hook_timeout` | whole or fractional number of seconds, 1 to 600: how long each hook command gets | `10` |

Example `.nar/config.toml`:

```toml
model = "openrouter/auto"
approval = "auto-edit"
```

Unknown keys, invalid values and malformed TOML stop `nar` with a one-line error and exit
code 1. `nar --show-config` prints each effective key with its source (`default`, the user
file, the project file or `command line`).

`--provider`, `--model`, `--approval` and `--language` override the files for one run. A `model` set in a file is kept
when only `--provider` is given, so pass both flags when switching to a provider that does not
know that model.

## Reply language

`nar` tells the model to always reply in the language of the `language` setting: `es` (Spanish,
the default) or `en` (English). So, with no configuration, a question written in English is
answered in Spanish. Set it for every project in `~/.nar/config.toml`, for one project in
`.nar/config.toml`, or for one run with `--language`:

```toml
language = "en"
```

```bash
nar --language en "what does README.md say?"
```

- The same rule is given to every subagent, and the summary written when the conversation is
  compacted uses the same language.
- Code, identifiers, file names, paths, commands, tool arguments and quoted text are left
  unchanged.
- The rule is part of the system prompt, which is built once, so the language does not change
  during a session. Start `nar` again with another `--language` to change it.
- Only the model's replies change. `nar`'s own messages (errors, warnings, `/help`) stay in
  English for now.
- It is an instruction to the model, so an `AGENTS.md` that asks for something else, such as
  commit messages in English, still applies.

## Approvals

Tools that change things (`write_file`, `edit_file` and `shell`) need your approval. The
`approval` setting, or `--approval` for one run, chooses how much `nar` asks:

| Level | `nar` asks before | Example |
|---|---|---|
| `ask` (default) | every file write, file edit and shell command | `nar "rename the helper"` |
| `auto-edit` | shell commands, and file changes outside the project folder or inside `.git` or `.nar` | `nar --approval auto-edit "fix the typo in README.md"` |
| `yolo` | nothing | `nar --approval yolo "run the tests and fix them"` |

`auto-edit` applies edits without asking only for files inside the folder where you started
`nar` (symbolic links and `..` that leave it do not count). `.git` and `.nar` are always asked
about: git hooks and `.nar/config.toml` can make later commands run without another question.
Other files that run code, such as `package.json` or CI workflows, are not special, so use
`ask` if you want to see every edit.

When `nar` asks, it shows what the call would do and waits on standard error:

```text
-> edit_file(path="hello.txt", old_string="world", new_string="nar")
Approval needed: Edit hello.txt (+1 -1)
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-hello world
+hello nar
Allow edit_file? [y]es / [n]o / [a]lways allow edit_file this session:
```

Type a letter and press Enter (`yes`, `no` and `always` also work; an empty or unknown answer
asks again, so a stray Enter never approves).

- `y` runs this call.
- `n` rejects it. `nar` then asks for an optional reason (Enter to skip). The model is told the
  call was not run, with your reason if you gave one, so it can propose something else.
  Nothing is changed on disk.
- `a` runs this call and every later call of the same tool in this session, without asking.
  It applies to the tool, not to a file or command: after `a` on `shell`, every later command
  runs, whatever it is (the safety list of the shell tool still applies). Starting `nar` again
  asks again.
- End of input (Ctrl+D, or Ctrl+Z then Enter on Windows) rejects the call. Ctrl+C cancels the
  whole request, as everywhere else.

What you see for each built-in tool:

| Tool | Preview |
|---|---|
| `edit_file` | the diff, with the number of added and removed lines |
| `write_file` | `Create <path> (N lines)` with the beginning of the content, or `Overwrite <path>` with a diff against the current file; a path outside the project is shown in full |
| `shell` | the shell, the timeout and the command text exactly as it will run |
| an MCP tool | `MCP <server>: <tool>` and the arguments as indented JSON |
| any other tool | its name and arguments |

Long previews are cut (60 lines of text, 100 lines of diff) with a note and
`(preview shortened)`. A call that cannot run anyway (the text to replace is not found, a blocked
command, a folder used as a file) is not shown to you; the model gets the error directly.

Prompts and previews go to standard error, so `nar "..." > answer.txt` still saves only the
answer. If `nar` cannot ask because standard input or standard error is not a terminal (a pipe,
a redirect, CI), it does not wait: the call is refused, the model is told how to allow it, and
the run goes on, so the exit code stays 0. In that case use `--approval auto-edit` or
`--approval yolo`, or set `approval` in `.nar/config.toml`.

## Hooks

A project can run its own shell command at five points of the loop. Hooks are configured per event
in `.nar/config.toml` (or `~/.nar/config.toml`), as a list of commands run in order:

```toml
[hooks]
after_tool = ["ruff check --fix ."]
before_tool = ["node scripts/guard.js"]
on_error = ["notify-send 'nar hit an error'"]
```

| Event | When it runs | Can it block? |
|---|---|---|
| `before_agent` | once, before the first model call of a request | no |
| `before_tool` | before each tool call, after its approval | **yes** |
| `after_tool` | after each tool call, with its result | no |
| `after_agent` | once, when the request ends (answer, turn limit, loop, Ctrl+C or error) | no |
| `on_error` | for each failing tool result and for a provider failure | no |

Every hook gets one JSON object on standard input, on a single line:

| Event | Fields |
|---|---|
| `before_agent` | `event`, `prompt`, `cwd` |
| `before_tool` | `event`, `tool`, `arguments`, `cwd` |
| `after_tool` | `event`, `tool`, `arguments`, `is_error`, `output` (cut at 4,000 characters), `cwd` |
| `after_agent` | `event`, `prompt`, `outcome` (`finished`, `turn_limit`, `loop`, `cancelled`, `error`), `turns`, `cwd` |
| `on_error` | `event`, `where` (`tool` or `provider`), `message`, `cwd` |

- **A `before_tool` hook refuses a call by exiting with code `2`.** The tool does not run and what
  the hook printed is what the model receives, so a project rule is a rule and not a suggestion. Any
  other non-zero code — including `1`, which the shell itself returns when the command does not
  exist — means the hook failed: the terminal warns that it did not block and the call runs, because
  a mistyped path must not take the tools away.
- **A broken hook never breaks `nar`.** A command that is missing, crashes or exceeds
  `hook_timeout` produces a warning and the request goes on, and a `before_tool` hook that is broken
  or mistyped never blocks a call.
- **What a hook prints is shown to you**, as dim lines on standard error (cut at 2,000 characters
  per hook). The model only sees it when a `before_tool` hook blocks a call.
- Hooks run **synchronously**, in the project folder, through the same shell the `shell` tool uses,
  and they inherit the environment. A slow hook slows the request.
- With no `[hooks]` table nothing runs and no extra process is started.

### Example: run `ruff` after every edit

`.nar/config.toml`:

```toml
[hooks]
after_tool = ["ruff check --fix . 2>&1"]
```

Git Bash:

```bash
printf '%s\n' '[hooks]' 'after_tool = ["ruff check --fix . 2>&1"]' > .nar/config.toml
nar "add a docstring to src/app.py"
```

PowerShell:

```powershell
Set-Content .nar/config.toml "[hooks]`nafter_tool = ['ruff check --fix . 2>&1']"
nar "add a docstring to src/app.py"
```

After the edit, `hook after_tool: ruff check --fix . 2>&1` and ruff's own output appear on standard
error, and the answer keeps standard output clean.

## Long conversations

Before each model call `nar` compares the size of the conversation (system prompt, tool
definitions and history) with `context_threshold` percent of `context_window`. Over that limit:

1. **Pruning.** The results of the oldest tool calls are replaced by a marker that tells the model
   to call the tool again if it needs them. The latest results, up to a quarter of the window,
   stay complete. A new result always reaches the model; if it is too large for the context, it is
   cut with a note telling the model to ask for a smaller part. This does not call the model.
2. **Compaction.** If that is not enough, `nar` asks the same model for a summary of the older
   messages (one extra call) and replaces them with it, keeping the latest messages. The request
   in progress is never lost.

Each step prints a line on standard error, for example
`context: compacted (102,400 -> 21,000 tokens)`. If the summary fails, `nar` warns and continues
with the history it has. Cancelling a request (Ctrl+C), or a provider error, also undoes any
pruning or compaction that request did; files changed by tools stay changed.

In interactive mode, a line after each request shows how full the context is
(`context: 12,300 / 128,000 tokens (9%)`), and `/compact` summarizes the conversation on request.
The count is the usage the provider reported plus an estimate of what came after it (about 4
characters per token), so it is approximate.

`nar` cannot know the real size of every model, so `context_window` has a conservative default
per provider. Set it in `.nar/config.toml` to match your model:

| Provider | Default `context_window` |
|---|---|
| `anthropic` | `200000` |
| `openrouter` | `128000` |
| `deepseek` | `128000` |
| `ollama` | `4096` |

With Ollama, raise it only if the server also uses a larger context (`OLLAMA_CONTEXT_LENGTH`):
beyond its real context Ollama drops the oldest text without any error.

## Sessions

Each conversation is saved automatically, in interactive and in single mode, to
`~/.nar/sessions/<id>.json` (or under `NAR_HOME`): before each model call and when each request
ends. If the terminal closes, you lose at most the turn in progress. A cancelled request leaves no
trace, and a run where nothing was sent creates no file.

| Command | What it does |
|---|---|
| `nar --resume` | Continue the latest session of the current folder |
| `nar --resume <id>` | Continue that session; the beginning of the id is enough if only one matches |
| `nar --resume <id> "prompt"` | The same, with one prompt, and exit |
| `/sessions` | In interactive mode: list the saved sessions (the current one has a `*`) |
| `/resume [id]` | In interactive mode: save this session and switch to that one, or to the latest other session of this folder |

`--resume` reads the next word as an id, so `nar --resume "a prompt"` fails with `no session`; to
continue the latest session with a prompt, start `nar --resume` and type it.

When you resume, the earlier requests, answers and tool calls are shown (the last 20 messages, on
standard error) and the conversation continues. The system prompt is built again for the current
folder, and the provider and model come from your current configuration. A session resumed in
another folder shows a warning, tools run in the current folder, and the session then belongs to
it. "Always allow" answers are not restored, so `nar` asks again, and the `todos` list is not
saved. If two `nar` processes use the same session, the last save wins.

**Session files hold the whole conversation in plain text, including file contents and command
output, which can include secrets.** On Linux and macOS only you can read them; on Windows they
rely on the permissions of your user folder. `nar` never removes them: delete the files you no
longer want. If a session cannot be saved, `nar` warns and keeps working.

## Checkpoints

A **session** is the whole conversation. A **checkpoint** is a point inside it that you can go
back to. In interactive mode:

| Command | What it does |
|---|---|
| `/checkpoint [name]` | Save the conversation at this point, with a number and an optional name |
| `/checkpoints` | List the checkpoints of this session |
| `/restore <id or name>` | Go back: later messages are discarded and the agent continues from there |

**Only the conversation goes back. Files changed by tools and commands already run stay as they
are**, so the model may remember less than what is on disk; ask it to read the files again if it
matters.

Messages after the last checkpoint are lost on a restore, so save a checkpoint first if you may
want them. Later checkpoints are kept, so `/restore 2` after `/restore 1` undoes the restore.
Checkpoints last with their session and are available again after `nar --resume` or `/resume`. They
are saved in `~/.nar/sessions/checkpoints/<session id>.json`, in plain text like sessions, with the
same permissions; `nar` never removes them.

## Subagents

To keep long searches out of the conversation, the model can hand a task to a subagent with the
`task` tool. A subagent has its own conversation, starts knowing only the prompt it was given, and
returns only its final answer. So the main context grows by that answer, however much the subagent
read. (Why this helps, and how other agents do it, is in
[`docs/SUBAGENTES.md`](https://github.com/rojb/nar-specs/blob/main/docs/SUBAGENTES.md).)

| Type | Tools | For |
|---|---|---|
| `explorer` | `read_file`, `grep`, `glob`, `list_dir` | searching and understanding code; it cannot change anything |
| `general` | every other tool, including MCP and custom tools, but not `task` or `todos` | a separate piece of work that also changes things |

- The model decides when to delegate, and it can choose not to.
- While it works you see one updating line. Its text and its tool activity are not shown, and they are
  not saved in the session. When it ends, the result line says, for example,
  `explorer: 12 tool calls, 3 turns`.
- `general` asks for approval like the main agent, and "always" answers are shared. Hooks run too:
  `before_tool` can block a subagent's call, and `before_agent` and `after_agent` fire for each
  subagent as well.
- A subagent stops after `subagent_max_turns` model calls (default 15). The main agent is told that
  the answer may be incomplete. Subagents cannot start other subagents, and they run one at a time.
- Ctrl+C cancels the whole request.
- **A subagent makes its own model calls.** The main context stays small, but a delegated task can
  use more tokens in total.

## Custom tools

Add a tool without changing `nar`: drop a Python file that defines a subclass of `Tool` in one of
these folders, and start `nar`.

| Folder | Scope | Runs |
|---|---|---|
| `~/.nar/tools/` | all your projects | without asking |
| `.nar/tools/` | this project | after you allow it |

There is a working example in [`examples/tools/word_count.py`](examples/tools/word_count.py):

```python
from nar.tools import Tool, ToolContext, ToolResult


class WordCountTool(Tool):
    name = "word_count"
    description = "Count the lines and words of a text file."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    def execute(self, arguments: dict, context: ToolContext) -> ToolResult:
        text = (context.cwd / arguments["path"]).read_text(encoding="utf-8")
        return ToolResult.ok(f"{len(text.split())} words")
```

- A file can define several tools. Files starting with `_` are skipped, and each tool is created
  with no arguments.
- **Set `requires_approval = True` on a tool that changes anything.** It is `False` by default, so
  otherwise the model can call the tool without asking you.
- A file that fails (an error, `sys.exit`, a bad tool) prints one warning and the others still
  load. A name that is already taken keeps the existing tool, so a custom tool cannot replace
  `read_file` or any other.

**A tool file is ordinary Python and runs with your permissions as soon as it loads.** That is why
the files of `.nar/tools/`, which come with whatever repository you cloned, do not run until you
say so. `nar` lists them and asks once for that folder and those contents; if a file is added or
changed, it asks again. Answers are kept in `~/.nar/trusted_tools.json`. Where `nar` cannot ask (a
pipe, CI) they are skipped with a warning, unless you pass `--trust-project-tools`. Neither
`.nar/config.toml` nor `--approval yolo` can allow them, because a repository can ship its own
configuration.

## Skills

A skill is a folder with a `SKILL.md` file of specialized instructions that the agent reads only
when a request needs them. Put skill folders in one of these places and start `nar`:

| Folder | Scope |
|---|---|
| `~/.nar/skills/<name>/SKILL.md` (`$NAR_HOME/skills/` when set) | all your projects |
| `.nar/skills/<name>/SKILL.md` | this project |

There is a working example in [`examples/skills/changelog/`](examples/skills/changelog/SKILL.md):

```markdown
---
name: changelog
description: Write or update CHANGELOG.md entries from recent git commits. Use when the user asks for a changelog.
---

# Writing a changelog entry

1. List what changed with `git log <last tag>..HEAD --oneline`.
2. Group the commits with the headings in [`template.md`](template.md).
```

- **The file starts with YAML frontmatter** between two `---` lines, then the instructions in
  Markdown. `name` and `description` are required. `name` uses lowercase letters, digits and
  hyphens (at most 64 characters) and must equal the folder name; `description` is at most 1024
  characters and should say what the skill does and when to use it. Other keys are ignored.
- **The folder can hold other files** (templates, reference notes, scripts) that the instructions
  refer to. The agent gets the folder's absolute path with the instructions, so it can read them.
- **The agent sees only the name and description.** They are listed in the system prompt, and when
  a request matches, the agent calls the `skill` tool to read the instructions. That tool needs no
  approval and reads the file each time, so edits to a `SKILL.md` apply without a restart.
- **Project skills win.** If both folders have a skill with the same name, the project one is used
  and the other is ignored. A skill added after `nar` started needs a restart to be listed.
- **A broken skill is skipped with one warning** that names the file and the reason (no
  frontmatter, name different from the folder, description too long, ...); the other skills still
  load.
- `/skills` lists the available skills with their origin (`project` or `user`).
  `/skill <name>` adds a skill's instructions to your next request; `/skill <name> <request>`
  sends the request at once. `/clear` drops a skill that was waiting for the next request.

**`nar` never runs anything from a skill folder.** The files are only text. A command or script
that the instructions mention runs only if the agent calls a tool for it, and then it goes through
the usual approvals. Project skills load without asking, like `AGENTS.md`, so read the
`SKILL.md` of a repository you do not trust before you rely on it.

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
| `todos` | Keep the agent's task list for the session, shown as a `[ ]`/`[>]`/`[x]` checklist. | no |
| `memory` | Save, delete and list short facts that are given to the agent in every new session. | no |
| `shell` | Run a command line in PowerShell (Windows) or bash/sh; returns exit code, stdout and stderr. | yes |
| `skill` | Load the instructions of a skill (see "Skills"); only registered when a skill exists. | no |

`write_file`, `edit_file` and `shell` require approval: `nar` asks before running them (see
"Approvals"). `scripts/run_tool.py` always runs them.

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

For multi-step tasks the agent writes a plan with `todos`; each time it updates the list, the
checklist appears below the tool line (`[ ]` pending, `[>]` in progress, `[x]` done). The list
lasts for the session only.

`memory` keeps up to 50 short facts (key up to 64 characters, value up to 500) in
`~/.nar/memory.json` (`$NAR_HOME/memory.json` when set). They are added to the agent's system
prompt at the start of every session, so a preference you state once ("answer in Spanish") is
remembered next time. The file is plain JSON: open it to review what was saved, edit it, or
delete it to forget everything. If it is not valid JSON, `nar` warns at startup, starts without
memory and never overwrites it.

```bash
export NAR_HOME=$(mktemp -d)
python scripts/run_tool.py memory '{"action": "save", "key": "language", "value": "Answer in Spanish"}'
python scripts/run_tool.py memory '{"action": "list"}'
python scripts/run_tool.py todos '{"todos": [{"content": "Read the code", "status": "completed"}, {"content": "Write the summary", "status": "in_progress"}]}'
```

### The shell tool

`shell` runs one command line and returns its exit code, standard output and standard error.
Each call starts a fresh shell in the working directory, so `cd` and variables do not carry
over; standard input is closed, so interactive programs cannot work.

- **Shell used:** on Windows, PowerShell 7 (`pwsh`) when installed, otherwise Windows PowerShell
  (`powershell`); on Linux and macOS, `bash`, otherwise `sh`. The tool description tells the
  model which one it is.
- **Timeout:** `timeout` seconds (default 120, at most 600). A command that is still running
  is killed together with the processes it started, and the call returns an error that keeps
  the output printed so far.
- **Output limit:** each stream keeps its first 4,000 and last 10,000 bytes, with a
  `[... N bytes omitted ...]` marker in between.
- **Exit codes:** a non-zero exit code is a normal result, not a tool error (`pytest` exits 1
  when tests fail). The first line of the output is `exit code: N` and the terminal summary
  reads `exit N · ...`.
- **Blocklist:** a few destructive commands (`rm -rf /`, formatting a disk, shutting the
  machine down, ...) are refused even at `yolo`. It is a safety net against mistakes, not a
  sandbox: obfuscated or indirect commands are not caught.
- **Approval:** `shell` requires approval at every level except `yolo`, including `auto-edit`;
  `scripts/run_tool.py` always runs it.

```bash
python scripts/run_tool.py shell '{"command": "python --version"}'
python scripts/run_tool.py shell '{"command": "python --version", "timeout": 30}'
```

## MCP servers

`nar` can connect to external [Model Context Protocol](https://modelcontextprotocol.io) (MCP)
servers, such as Linear, and offer their tools to the model next to the built-in ones. Servers
are defined in `~/.nar/config.toml` or `.nar/config.toml`, one table per server:

```toml
# a program nar starts and talks to over its standard input and output (stdio)
[mcp_servers.files]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "."]

# a server that is already running somewhere (streamable HTTP)
[mcp_servers.linear]
url = "https://mcp.linear.app/mcp"
headers = { Authorization = "Bearer ${LINEAR_API_KEY}" }
```

| Key | Meaning | Default |
|---|---|---|
| `command` | stdio: program to start (`npx` works on Windows too) | none |
| `args` | stdio: its arguments, a list of strings | `[]` |
| `env` | stdio: extra environment variables for the program, a table of strings | `{}` |
| `url` | HTTP: the server's streamable HTTP endpoint | none |
| `headers` | HTTP: headers sent with every request, a table of strings | `{}` |
| `enabled` | `false` skips the server (it is still listed by `/mcp`) | `true` |
| `enabled_tools` | list of the server's own tool names; only these are offered to the model | all tools |
| `startup_timeout` | seconds to connect and list the tools | `30` |
| `tool_timeout` | seconds one tool call may take | `60` |

Set exactly one of `command` or `url`. The server name (the part after `mcp_servers.`) uses
letters, digits, `_` and `-` (at most 32 characters); `my-srv` and `my_srv` cannot both be used.
Entries of the user file and the project file are merged key by key, so a project can set
`enabled = false` for a server defined in the user file.

**Secrets.** Never write a token into a configuration file. In `command`, `args`, `env`, `url` and
`headers`, `${NAME}` is replaced by the value of the environment variable `NAME` when `nar`
starts the server. If the variable is unset or empty, that server is skipped with a warning that
names the variable, and no request is sent with an empty credential. `nar --show-config` prints
`${NAME}` as written and shows any other value of `env` or `headers` as `********`. A program
started over stdio receives only a minimal environment (`PATH` and similar) plus its `env`, not
the rest of your shell; to pass a variable on, write `env = { KEY = "${KEY}" }`.

**What the model sees.** Each tool of each connected server is offered as
`mcp__<server>__<tool>`, for example `mcp__linear__list_issues`. Characters other than letters,
digits and `_` become `_`, names are at most 64 characters, and the server is always called with
the tool's original name. The server's description and parameter schema are passed on as they
are (a description is cut at 2,000 characters), and arguments are checked against the schema before
anything is sent. Text results are returned as text; an image, audio clip or binary resource is
replaced by a one-line description such as `[image image/png, 40000 bytes, not shown]`. A
result is cut at the same 30,000 characters as every other tool.

**Approval.** Every MCP tool asks first at `ask` and `auto-edit` and runs unasked only at `yolo`.
The question shows `MCP <server>: <tool>` and the arguments. `a` ("always allow this tool this
session") applies to that one tool. A server's own hints, such as "read only", are not trusted and
do not skip the question. To keep the list short, set `enabled_tools`.

**Checking the servers.** In interactive mode `/mcp` prints, for each server, its name,
transport (`stdio` or `http`), status (`connected`, `failed` or `disabled`) and its tools; for a
failed server it prints the reason and, for a stdio server, the log file. `/mcp <name>` prints one
server. The command does not call the model or contact the servers, and never prints headers or
environment values.

```text
nar> /mcp
linear  (http)  connected
  3 tools
    mcp__linear__get_issue
    mcp__linear__list_issues
    mcp__linear__save_issue
jira  (http)  failed
  authentication rejected (HTTP 401)
```

**When something is wrong.** A server that cannot be used (unknown command, rejected token,
unreachable URL, timeout, not an MCP server) gives one warning at startup and is skipped; `nar`
and the other servers keep working. What a stdio server writes to its standard error is kept in
`~/.nar/mcp-logs/<server>.log` (under `NAR_HOME` if set), not in the terminal. The first start of
`npx` can take a while because it downloads the package: raise `startup_timeout`. Behind a
company proxy that replaces TLS certificates, set `SSL_CERT_FILE` to your CA bundle. If a server
dies during a session its tools answer with "no longer connected" and `/mcp` shows it as failed;
`nar` does not reconnect, so start it again by restarting `nar`. Ctrl+C cancels a running MCP
call within a second and keeps the server connected. When `nar` ends, every server it started is
stopped.

### Demo: Linear

Linear's official MCP server lives at `https://mcp.linear.app/mcp`. This lets `nar` list the
issues of a Linear project.

*What you provide:* a Linear personal API key (Linear > Settings > Account > Security & access;
a key limited to read access is enough for the demo) in the environment variable
`LINEAR_API_KEY`. Nothing else is required: no Node.js and no browser.

```bash
# Git Bash / Linux / macOS (this terminal only)
export LINEAR_API_KEY=lin_api_xxxxxxxx
```

```powershell
# Windows PowerShell (this window only)
$env:LINEAR_API_KEY = "lin_api_xxxxxxxx"
# kept for new terminals: setx LINEAR_API_KEY "lin_api_xxxxxxxx", then open a new terminal
```

Add this to `.nar/config.toml` of the project where you run `nar`:

```toml
[mcp_servers.linear]
url = "https://mcp.linear.app/mcp"
headers = { Authorization = "Bearer ${LINEAR_API_KEY}" }
```

Check the configuration without starting anything (`nar --show-config` shows
`Bearer ${LINEAR_API_KEY}`, never the key), then start `nar`:

```bash
nar --show-config
nar
```

`nar` prints `connecting MCP servers: linear ...`. Type `/mcp`: `linear` should be `http` and
`connected`, with its tools. Then ask for something Linear knows:

```text
nar> list the issues of the Linear project nar in team RRG
```

`nar` shows `MCP linear: list_issues` with the arguments and waits for `y`; the answer is built
from the real issues. Tip: Linear documents a read-only variant of the endpoint,
`https://mcp.linear.app/mcp/readonly`, which exposes no tool that changes anything; use it as
`url` if you only want to read.

*If Linear rejects the key:* `/mcp` shows `authentication rejected (HTTP 401)`. Linear documents
that its server accepts a personal API key as a Bearer token, but if your workspace does not,
use the fallback, which logs in through your browser once. It needs Node.js 18 or newer
(for `npx`) and a browser:

```toml
[mcp_servers.linear]
command = "npx"
args = ["-y", "mcp-remote", "https://mcp.linear.app/mcp"]
startup_timeout = 120
```

The first start opens a browser tab to log in to Linear (the long `startup_timeout` leaves time
for that); `mcp-remote` remembers the login afterwards.

## Environment variables

| Variable | Effect |
|---|---|
| `NAR_HOME` | Use this folder instead of `~/.nar` (handy for trying things without touching your real home). |
| `LINEAR_API_KEY` | Only for the Linear demo: referenced from `[mcp_servers.linear]` as `${LINEAR_API_KEY}`; `nar` itself does not read it. |
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
