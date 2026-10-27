"""Load, merge and validate configuration files."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from nar.config import paths
from nar.config.models import NarConfig
from nar.errors import ConfigError

DEFAULT_SOURCE = "default"
COMMAND_LINE_SOURCE = "command line"


@dataclass(frozen=True)
class LoadedConfig:
    """The validated configuration plus where each dotted key came from."""

    config: NarConfig
    provenance: dict[str, str] = field(default_factory=dict)
    user_path: Path | None = None
    project_path: Path | None = None

    def describe(self, cwd: Path | None = None) -> list[str]:
        """Lines ``key = value  (source)`` for ``--show-config``."""
        flat: dict[str, Any] = {}
        _flatten(self.config.model_dump(mode="json"), "", flat)
        lines = []
        for key, value in flat.items():
            source = self.provenance.get(key, DEFAULT_SOURCE)
            lines.append(f"{key} = {_format_value(value)}  ({_display_source(source, cwd)})")
        return lines


def load_config(cwd: Path, overrides: dict[str, Any] | None = None) -> LoadedConfig:
    """Merge defaults <- user <- project <- command line, then validate once.

    ``overrides`` holds command-line values; ``None`` entries are ignored.
    """
    user_path = paths.user_config()
    project_path = paths.project_config(cwd)

    # Without validation, settings resolved later (such as `model`) stay None and are left out.
    defaults = NarConfig.model_construct().model_dump(mode="json", exclude_none=True)
    provenance: dict[str, str] = {}
    _flatten(defaults, "", provenance, DEFAULT_SOURCE)

    merged = defaults
    for path in (user_path, project_path):
        data = _read_toml(path)
        if data:
            merged = _merge(merged, data, "", str(path), provenance)
    flags = {key: value for key, value in (overrides or {}).items() if value is not None}
    if flags:
        merged = _merge(merged, flags, "", COMMAND_LINE_SOURCE, provenance)

    try:
        config = NarConfig.model_validate(merged)
    except ValidationError as exc:
        raise _translate(exc, provenance) from exc
    return LoadedConfig(config, provenance, user_path, project_path)


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        # utf-8-sig drops a leading BOM, which Windows PowerShell 5.1 and old Notepad write.
        return tomllib.loads(path.read_bytes().decode("utf-8-sig"))
    except tomllib.TOMLDecodeError as exc:
        match = re.search(r"line (\d+)", str(exc))
        where = f"line {match.group(1)}" if match else "unknown line"
        raise ConfigError(f"invalid TOML in {path} ({where}): {exc}", path=path) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}", path=path) from exc


def _flatten(value: Any, prefix: str, out: dict[str, Any], source: str | None = None) -> None:
    """Flatten nested dicts into dotted keys; empty dicts and non-dicts are leaves.

    With ``source`` set, ``out`` maps keys to that source; otherwise to the values.
    """
    if isinstance(value, dict) and value:
        for key, child in value.items():
            _flatten(child, f"{prefix}.{key}" if prefix else key, out, source)
    elif prefix:
        out[prefix] = value if source is None else source


def _merge(
    base: dict[str, Any],
    override: dict[str, Any],
    prefix: str,
    source: str,
    provenance: dict[str, str],
) -> dict[str, Any]:
    """Deep-merge dicts; scalars and lists are replaced. Records provenance."""
    result = dict(base)
    for key, value in override.items():
        dotted = f"{prefix}.{key}" if prefix else key
        current = result.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            provenance.pop(dotted, None)  # a former leaf becomes a parent
            result[key] = _merge(current, value, dotted, source, provenance)
        else:
            for old in [k for k in provenance if k == dotted or k.startswith(dotted + ".")]:
                del provenance[old]
            _flatten(value, dotted, provenance, source)
            result[key] = value
    return result


def _source_for(loc: tuple[Any, ...], provenance: dict[str, str]) -> str:
    parts = [str(p) for p in loc]
    while parts:
        key = ".".join(parts)
        if key in provenance:
            return provenance[key]
        parts.pop()
    return DEFAULT_SOURCE


def _allowed_values(loc: tuple[Any, ...]) -> list[str] | None:
    if len(loc) != 1 or loc[0] not in NarConfig.model_fields:
        return None
    annotation = NarConfig.model_fields[str(loc[0])].annotation
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return [str(member.value) for member in annotation]
    return None


def _translate(exc: ValidationError, provenance: dict[str, str]) -> ConfigError:
    messages = []
    first_path: Path | None = None
    for err in exc.errors():
        loc = tuple(err["loc"])
        key = ".".join(str(p) for p in loc)
        source = _source_for(loc, provenance)
        where = "built-in defaults" if source == DEFAULT_SOURCE else source
        if source not in (DEFAULT_SOURCE, COMMAND_LINE_SOURCE) and first_path is None:
            first_path = Path(source)
        if err["type"] == "extra_forbidden":
            messages.append(f"unknown setting '{key}' in {where}")
            continue
        allowed = _allowed_values(loc)
        if allowed:
            messages.append(
                f"invalid value {json.dumps(err.get('input'))} for setting '{key}' in {where}; "
                f"allowed values: {', '.join(allowed)}"
            )
        else:
            messages.append(f"invalid setting '{key}' in {where}: {err['msg']}")
    return ConfigError("; ".join(messages), path=first_path)


def _format_value(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def _display_source(source: str, cwd: Path | None) -> str:
    if source in (DEFAULT_SOURCE, COMMAND_LINE_SOURCE) or cwd is None:
        return source
    try:
        return Path(source).relative_to(cwd).as_posix()
    except ValueError:
        return source
