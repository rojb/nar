"""Configuration model. Later changes add their settings here."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator


class ApprovalLevel(StrEnum):
    ASK = "ask"
    AUTO_EDIT = "auto-edit"
    YOLO = "yolo"


class Language(StrEnum):
    """The language the model replies in (RRG-97)."""

    ES = "es"
    EN = "en"

    @property
    def label(self) -> str:
        """The language named in English, as the prompts name it."""
        return {Language.ES: "Spanish", Language.EN: "English"}[self]


class HookEvent(StrEnum):
    """The moments a project may run one of its own commands (RRG-72)."""

    BEFORE_AGENT = "before_agent"
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    AFTER_AGENT = "after_agent"
    ON_ERROR = "on_error"


class ProviderName(StrEnum):
    ANTHROPIC = "anthropic"
    OPENROUTER = "openrouter"
    OLLAMA = "ollama"
    DEEPSEEK = "deepseek"


# Used when no configuration file or flag sets `model`.
DEFAULT_MODELS = {
    ProviderName.ANTHROPIC: "claude-sonnet-5-5",
    ProviderName.OPENROUTER: "openrouter/auto",
    ProviderName.OLLAMA: "qwen2.5-coder",
    ProviderName.DEEPSEEK: "deepseek-flash",
}


# Used when no configuration file sets `context_window`, in tokens. Conservative: nar cannot
# know the real model (openrouter/auto picks one per call; Ollama depends on the server).
DEFAULT_CONTEXT_WINDOWS = {
    ProviderName.ANTHROPIC: 200_000,
    ProviderName.OPENROUTER: 128_000,
    ProviderName.OLLAMA: 4_096,
    ProviderName.DEEPSEEK: 128_000,
}


SERVER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


class McpServerConfig(BaseModel):
    """One external MCP server (``[mcp_servers.<name>]``): a stdio command or an HTTP URL."""

    model_config = ConfigDict(extra="forbid")

    # stdio transport
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    # streamable HTTP transport
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    # both transports
    enabled: bool = True
    enabled_tools: list[str] | None = None
    startup_timeout: int | float = Field(default=30, gt=0)
    tool_timeout: int | float = Field(default=60, gt=0)

    @model_validator(mode="after")
    def _exactly_one_transport(self) -> McpServerConfig:
        if (self.command is None) == (self.url is None):
            raise ValueError("exactly one of 'command' or 'url' must be set")
        if self.command is not None:
            transport, foreign = "command", ("url", "headers")
        else:
            transport, foreign = "url", ("command", "args", "env")
        for key in foreign:
            if key in self.model_fields_set:
                raise ValueError(f"'{key}' cannot be used together with '{transport}'")
        return self

    @property
    def transport(self) -> str:
        return "stdio" if self.command is not None else "http"


class NarConfig(BaseModel):
    """Effective nar settings. Unknown top-level keys are rejected.

    Settings whose default depends on another setting default to ``None`` and
    are resolved after validation (the loader leaves them out of its defaults).
    """

    model_config = ConfigDict(extra="forbid")

    provider: ProviderName = ProviderName.OPENROUTER
    model: str | None = None
    approval: ApprovalLevel = ApprovalLevel.ASK
    # The language the model replies in (RRG-97).
    language: Language = Language.ES
    # Model calls the agent may make for one request (RRG-63).
    max_turns: int = Field(default=20, ge=1)
    # Model calls a subagent may make for one task (RRG-73).
    subagent_max_turns: int = Field(default=15, ge=1)
    # Size of the model's context in tokens, and the percentage of it where nar starts
    # reducing the conversation (RRG-70).
    context_window: int | None = Field(default=None, ge=1000)
    context_threshold: int = Field(default=80, ge=1, le=100)
    mcp_servers: dict[str, McpServerConfig] = Field(default_factory=dict)
    # Shell commands run around the loop, per event (RRG-72), and how long each gets.
    hooks: dict[HookEvent, list[str]] = Field(default_factory=dict)
    hook_timeout: float = Field(default=10, ge=1, le=600)

    @field_validator("hooks", mode="before")
    @classmethod
    def _known_hook_events(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        known = [event.value for event in HookEvent]
        for name in value:
            if name not in known:
                raise ValueError(
                    f"unknown setting 'hooks.{name}': use {', '.join(known[:-1])} or {known[-1]}"
                )
        return {HookEvent(name): commands for name, commands in value.items()}

    @field_validator("mcp_servers")
    @classmethod
    def _valid_server_names(cls, servers: dict[str, McpServerConfig]) -> dict[str, McpServerConfig]:
        seen: dict[str, str] = {}
        for name in servers:
            if not SERVER_NAME_PATTERN.match(name):
                raise ValueError(
                    f"invalid server name '{name}': use 1-32 letters, digits, '_' or '-', "
                    "starting with a letter or digit"
                )
            key = name.replace("-", "_")
            if key in seen:
                raise ValueError(
                    f"server names '{seen[key]}' and '{name}' are ambiguous: "
                    "they differ only by '-' versus '_'"
                )
            seen[key] = name
        return servers

    # Whether a file or a flag set context_window; False when it is the provider's default.
    _context_window_set: bool = PrivateAttr(default=False)

    @model_validator(mode="after")
    def _default_model_for_provider(self) -> NarConfig:
        if self.model is None:
            self.model = DEFAULT_MODELS[self.provider]
        self._context_window_set = self.context_window is not None
        if self.context_window is None:
            self.context_window = DEFAULT_CONTEXT_WINDOWS[self.provider]
        return self

    @property
    def context_window_set(self) -> bool:
        """True when the user chose the window, so a provider switch must keep it (RRG-90)."""
        return self._context_window_set
