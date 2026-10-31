"""Locations of the configuration files."""

from __future__ import annotations

import os
from pathlib import Path

NAR_HOME_ENV = "NAR_HOME"


def nar_home() -> Path:
    """The user-level nar folder: ``$NAR_HOME`` if set, else ``~/.nar``."""
    override = os.environ.get(NAR_HOME_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".nar"


def user_config() -> Path:
    return nar_home() / "config.toml"


def project_config(cwd: Path) -> Path:
    return cwd / ".nar" / "config.toml"


def mcp_log_dir() -> Path:
    """Where the standard error of stdio MCP servers is kept."""
    return nar_home() / "mcp-logs"
