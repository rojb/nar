"""Tools the user adds as Python files, without changing nar.

Importing a file runs it, and ``.nar/tools/`` comes with whatever repository was cloned, so
project files run only after the user allowed that folder with those exact contents. Nothing
in a configuration file can allow them, because a repository can ship its own.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from nar.config import paths
from nar.i18n import t, tn
from nar.tools.base import Tool
from nar.tools.registry import ToolRegistry

TRUST_VERSION = 1

Report = Callable[[str], None]


class TrustQuestion(Protocol):
    def available(self) -> bool: ...

    def ask(self, folder: Path, files: list[Path]) -> bool: ...


def find_tool_files(folder: Path) -> list[Path]:
    """The ``.py`` files directly in ``folder``, by name; ``_helper.py`` is left out."""
    if not folder.is_dir():
        return []
    return sorted(
        path for path in folder.glob("*.py") if path.is_file() and not path.name.startswith("_")
    )


def load_tool_file(path: Path) -> tuple[list[Tool], list[str]]:
    """Run ``path`` and create its tools; returns them and what went wrong.

    Only Ctrl+C gets out: a stray ``sys.exit`` in a tool file must not close nar.
    """
    name = f"nar_custom_tool_{hashlib.sha256(str(path).encode()).hexdigest()[:16]}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    wrote_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # no __pycache__ inside a repository
    sys.modules[name] = module  # dataclasses and typing look the module up while it runs
    try:
        spec.loader.exec_module(module)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:
        return [], [f"{type(exc).__name__}: {exc}"]
    finally:
        sys.dont_write_bytecode = wrote_bytecode
        sys.modules.pop(name, None)
    tools: list[Tool] = []
    problems: list[str] = []
    classes = [
        value
        for _, value in inspect.getmembers(module, inspect.isclass)
        if issubclass(value, Tool) and value.__module__ == name and not inspect.isabstract(value)
    ]
    if not classes:
        problems.append("it defines no Tool subclass")
    for tool_class in classes:
        try:
            tools.append(tool_class())
        except KeyboardInterrupt:
            raise
        except BaseException as exc:
            problems.append(f"cannot create {tool_class.__name__}: {type(exc).__name__}: {exc}")
    return tools, problems


def fingerprint(files: list[Path]) -> str:
    """Changes when a file is added, removed, renamed or edited."""
    digest = hashlib.sha256()
    for path in files:
        data = path.read_bytes()
        digest.update(f"{path.name}\0{len(data)}\0".encode())
        digest.update(data)
    return digest.hexdigest()


class TrustStore:
    """The project tool folders the user allowed, each with the contents it allowed."""

    def __init__(self, path: Path, warn: Report) -> None:
        self.path = path
        self.warn = warn

    def allowed(self, folder: Path, contents: str) -> bool:
        return self._load().get(str(folder)) == contents

    def remember(self, folder: Path, contents: str) -> None:
        # Imported here: the session package pulls in the provider types.
        from nar.session.store import write_private

        projects = self._load(quiet=True)
        projects[str(folder)] = contents
        text = json.dumps({"version": TRUST_VERSION, "projects": projects}, indent=1)
        try:
            write_private(self.path, text)
        except OSError as exc:
            self.warn(
                t("custom.cannot_remember", prefix=t("warning.prefix"), reason=exc.strerror or exc)
            )

    def _load(self, quiet: bool = False) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            projects = data["projects"]
            valid = (
                data["version"] == TRUST_VERSION
                and isinstance(projects, dict)
                and all(isinstance(value, str) for value in projects.values())
            )
            if not valid:
                raise ValueError("unexpected content")
        except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
            if not quiet:
                self.warn(
                    t("custom.ignoring", prefix=t("warning.prefix"), path=self.path, reason=exc)
                )
            return {}
        return dict(projects)


def load_custom_tools(
    registry: ToolRegistry,
    cwd: Path,
    question: TrustQuestion,
    trust_project: bool,
    note: Report,
    warn: Report,
) -> None:
    """Global tools first, then project tools if allowed; failures only warn."""
    home = paths.nar_home()
    global_folder = home / "tools"
    _register_files(registry, find_tool_files(global_folder), note, warn)
    project_folder = cwd / ".nar" / "tools"
    if project_folder.resolve() == global_folder.resolve():
        return
    files = find_tool_files(project_folder)
    if not files:
        return
    folder = project_folder.resolve()
    try:
        contents = fingerprint(files)
    except OSError as exc:
        reason = exc.strerror or exc
        warn(
            t(
                "custom.not_running_unreadable",
                prefix=t("warning.prefix"),
                folder=project_folder,
                reason=reason,
            )
        )
        return
    store = TrustStore(home / "trusted_tools.json", warn)
    if not store.allowed(folder, contents):
        if not trust_project:
            if not question.available():
                warn(
                    tn(
                        "custom.not_running_cannot_ask",
                        len(files),
                        prefix=t("warning.prefix"),
                        folder=project_folder,
                    )
                )
                return
            if not question.ask(project_folder, files):
                note(t("custom.skipped", folder=project_folder))
                return
        store.remember(folder, contents)
    _register_files(registry, files, note, warn)


def _register_files(registry: ToolRegistry, files: list[Path], note: Report, warn: Report) -> None:
    for path in files:
        tools, problems = load_tool_file(path)
        for problem in problems:
            warn(t("custom.file_problem", prefix=t("warning.prefix"), path=path, problem=problem))
        for tool in tools:
            try:
                _check_definition(tool)
                registry.register(tool)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                warn(t("custom.file_problem", prefix=t("warning.prefix"), path=path, problem=exc))
            else:
                note(t("custom.loaded", tool=tool.name, path=path))


def _check_definition(tool: Tool) -> None:
    """Catch what the registry accepts but would break later, on every model call."""
    missing = [key for key in ("name", "description", "parameters") if not hasattr(tool, key)]
    if missing:
        raise ValueError(f"{type(tool).__name__} does not set {', '.join(missing)}")
    if not isinstance(tool.description, str) or not tool.description.strip():
        raise ValueError(f"{type(tool).__name__}: description must be text")
