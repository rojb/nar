"""Replace ``${NAME}`` references in MCP server settings with environment values.

Expansion happens when a server is started, never when the configuration is
loaded, so the configuration (and ``--show-config``) only ever holds placeholders.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from nar.config import McpServerConfig
from nar.errors import NarError

_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class MissingVariable(NarError):
    """A ``${NAME}`` reference points to a variable that is unset or empty."""

    def __init__(self, name: str) -> None:
        super().__init__(f"environment variable {name} is not set")
        self.name = name


def expand_env(text: str, environ: Mapping[str, str]) -> str:
    """Replace each ``${NAME}`` in ``text``; raise :class:`MissingVariable` for unset or empty."""

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = environ.get(name)
        if not value:
            raise MissingVariable(name)
        return value

    return _REFERENCE.sub(replace, text)


def expand_server(config: McpServerConfig, environ: Mapping[str, str]) -> McpServerConfig:
    """A copy of ``config`` with every reference resolved; ``config`` itself is unchanged."""

    def one(text: str) -> str:
        return expand_env(text, environ)

    return config.model_copy(
        update={
            "command": None if config.command is None else one(config.command),
            "args": [one(item) for item in config.args],
            "env": {key: one(value) for key, value in config.env.items()},
            "url": None if config.url is None else one(config.url),
            "headers": {key: one(value) for key, value in config.headers.items()},
        }
    )


def referenced_values(config: McpServerConfig, environ: Mapping[str, str]) -> tuple[str, ...]:
    """Values of the variables ``config`` refers to, so output can be scrubbed of them."""
    texts = [config.command or "", config.url or "", *config.args]
    texts += [*config.env.values(), *config.headers.values()]
    names = {name for text in texts for name in _REFERENCE.findall(text)}
    return tuple(environ[name] for name in sorted(names) if environ.get(name))
