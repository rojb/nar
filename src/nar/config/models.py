"""Configuration model. Later changes add their settings here."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    # Entries stay loosely typed until RRG-74 defines their schema.
    mcp_servers: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _default_model_for_provider(self) -> NarConfig:
        if self.model is None:
            self.model = DEFAULT_MODELS[self.provider]
        return self
