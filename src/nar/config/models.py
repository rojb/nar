"""Configuration model. Later changes add their settings here."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ApprovalLevel(StrEnum):
    ASK = "ask"
    AUTO_EDIT = "auto-edit"
    YOLO = "yolo"


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
    # Model calls the agent may make for one request (RRG-63).
    max_turns: int = Field(default=20, ge=1)
    mcp_servers: dict[str, McpServerConfig] = Field(default_factory=dict)

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

    @model_validator(mode="after")
    def _default_model_for_provider(self) -> NarConfig:
        if self.model is None:
            self.model = DEFAULT_MODELS[self.provider]
        return self
