"""The ``nar`` command and its error boundary."""

from __future__ import annotations

import os
import sys
import tomllib
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from nar import __version__
from nar.cli import repl
from nar.config import ApprovalLevel, Language, NarConfig, ProviderName, load_config
from nar.config import paths as config_paths
from nar.context import ProjectInstructions, load_project_instructions
from nar.errors import NarError
from nar.i18n import DEFAULT_LANGUAGE, click_words, set_language, t, tn

if TYPE_CHECKING:
    from nar.session import Session

_LANGUAGES = tuple(language.value for language in Language)


def run_single(
    prompt: str,
    config: NarConfig,
    instructions: ProjectInstructions | None,
    resumed: Session | None = None,
    trust_project_tools: bool = False,
) -> int:
    """Run the agent on one prompt; returns the exit code."""
    # Imported here: the provider SDKs are slow to import and other commands do not need them.
    from nar.cli.runner import Outcome, connect_mcp, create_agent, open_session, run_request
    from nar.ui.agent_view import AgentView

    view = AgentView()
    agent = create_agent(
        config, Path.cwd(), instructions, view, trust_project_tools=trust_project_tools
    )
    with connect_mcp(config, agent, view):
        recorder = open_session(agent, view, Path.cwd(), resumed)
        if resumed is not None:
            view.note(tn("session.resumed", len(resumed.messages), id=resumed.id))
        outcome = run_request(agent, view, prompt, recorder)
        return 1 if outcome in (Outcome.TURN_LIMIT, Outcome.LOOP) else 0


def _find_session(resume: str, cwd: Path) -> Session:
    """The session ``--resume`` asks for; failing here needs no provider key or MCP server."""
    from nar.session import SessionFileError, SessionNotFound, SessionStore, sessions_dir

    store = SessionStore(sessions_dir())
    if resume:
        try:
            return store.find(resume)
        except SessionNotFound as exc:
            raise SessionFileError(t("session.resume_hint", message=exc.message)) from None
    session = store.latest(cwd)
    if session is None:
        raise SessionFileError(t("session.none_saved_in", cwd=cwd))
    return session


def build_command() -> click.Command:
    """The ``nar`` command, with its help texts in the interface language active right now.

    Built by a function, not at import time, so ``main`` can work out the language first.
    """

    @click.command(
        name="nar",
        context_settings={"help_option_names": ["--help"]},
        help=t("cli.help"),
    )
    @click.version_option(__version__, prog_name="nar")
    @click.option("--show-config", is_flag=True, help=t("cli.opt.show_config"))
    @click.option(
        "--provider",
        type=click.Choice([name.value for name in ProviderName]),
        help=t("cli.opt.provider"),
    )
    @click.option("--model", help=t("cli.opt.model"))
    @click.option(
        "--approval",
        type=click.Choice([level.value for level in ApprovalLevel]),
        help=t("cli.opt.approval"),
    )
    @click.option(
        "--language",
        type=click.Choice([language.value for language in Language]),
        help=t("cli.opt.language"),
    )
    @click.option(
        "--resume",
        is_flag=False,
        flag_value="",
        metavar="[ID]",
        help=t("cli.opt.resume"),
    )
    @click.option("--trust-project-tools", is_flag=True, help=t("cli.opt.trust_project_tools"))
    @click.argument("prompt", required=False)
    def cli(**params: Any) -> int:
        return _run(**params)

    return cli


def _run(
    prompt: str | None,
    show_config: bool,
    provider: str | None,
    model: str | None,
    approval: str | None,
    language: str | None,
    resume: str | None,
    trust_project_tools: bool,
) -> int:
    cwd = Path.cwd()
    # Invalid configuration stops startup here.
    loaded = load_config(
        cwd,
        overrides={
            "provider": provider,
            "model": model,
            "approval": approval,
            "language": language,
        },
    )
    set_language(loaded.config.language)
    if show_config:
        for line in loaded.describe(cwd):
            click.echo(line)
        return 0
    resumed = _find_session(resume, cwd) if resume is not None else None
    instructions = load_project_instructions(cwd)
    if prompt is not None:
        return run_single(prompt, loaded.config, instructions, resumed, trust_project_tools)
    repl.run(loaded.config, instructions, resumed, loaded, trust_project_tools)
    return 0


def _language_from_arguments(argv: Sequence[str]) -> str | None:
    """The value after ``--language`` (or ``--language=``), if it names a known language."""
    found: str | None = None
    items = list(argv)
    for index, item in enumerate(items):
        if item == "--":
            break
        if item == "--language" and index + 1 < len(items):
            found = items[index + 1]
        elif item.startswith("--language="):
            found = item.partition("=")[2]
    return found if found in _LANGUAGES else None


def _language_from_files(cwd: Path) -> str | None:
    """The ``language`` of the project file, else of the user file; every error is ignored."""
    for path in (config_paths.project_config(cwd), config_paths.user_config()):
        try:
            data = tomllib.loads(path.read_bytes().decode("utf-8-sig"))
        except Exception:
            continue
        value = data.get("language") if isinstance(data, dict) else None
        if value in _LANGUAGES:
            return value
    return None


def preresolve_language(argv: Sequence[str], cwd: Path | None = None) -> str:
    """Work out the interface language before Click parses anything or the config is loaded.

    In order: ``--language`` in the arguments, the ``language`` of the configuration files
    (errors in them are ignored), else Spanish. A value that is not ``es`` or ``en`` counts as
    not given.
    """
    try:
        place = cwd or Path.cwd()
        return _language_from_arguments(argv) or _language_from_files(place) or DEFAULT_LANGUAGE
    except Exception:  # best effort: never fail before the first line of output
        return DEFAULT_LANGUAGE


def _debug_enabled() -> bool:
    return os.environ.get("NAR_DEBUG") == "1"


def main(argv: list[str] | None = None) -> None:
    """Entry point: run the command and turn errors into one-line messages."""
    set_language(preresolve_language(sys.argv[1:] if argv is None else argv))
    with click_words.installed():
        _run_command(argv)


def _run_command(argv: list[str] | None) -> None:
    try:
        result = build_command().main(args=argv, standalone_mode=False)
    except click.exceptions.Abort:
        # Ctrl+C: Click turns KeyboardInterrupt into Abort. 130 is the shell's SIGINT code.
        click.secho(t("agent.cancelled"), fg="yellow", err=True)
        sys.exit(130)
    except click.ClickException as exc:
        exc.show()
        sys.exit(exc.exit_code)
    except NarError as exc:
        click.secho(f"error: {exc.message}", fg="red", err=True)
        if _debug_enabled():
            traceback.print_exc()
        sys.exit(exc.exit_code)
    except Exception as exc:
        message = t("main.unexpected", kind=type(exc).__name__, detail=exc)
        click.secho(message, fg="red", err=True)
        if _debug_enabled():
            traceback.print_exc()
        else:
            click.echo(t("main.debug_hint"), err=True)
        sys.exit(1)
    sys.exit(result if isinstance(result, int) else 0)
