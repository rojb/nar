"""Configuration model. Later changes add their settings here."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApprovalLevel(StrEnum):
    ASK = "ask"
    AUTO_EDIT = "auto-edit"
    YOLO = "yolo"


class NarConfig(BaseModel):
    """Effective nar settings. Unknown top-level keys are rejected."""

    model_config = ConfigDict(extra="forbid")

    provider: str = "openrouter"
    model: str = "openrouter/auto"
    approval: ApprovalLevel = ApprovalLevel.ASK
    # Entries stay loosely typed until RRG-74 defines their schema.
    mcp_servers: dict[str, dict[str, Any]] = Field(default_factory=dict)
