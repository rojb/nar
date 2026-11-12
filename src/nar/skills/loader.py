"""Find, parse and read ``SKILL.md`` files. Nothing here runs a file from a skill folder."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from nar.config.paths import nar_home
from nar.errors import NarError
from nar.i18n import t, t_en

SKILL_FILENAME = "SKILL.md"
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")
DELIMITER = "---"

Origin = Literal["project", "user"]


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    folder: Path
    origin: Origin

    @property
    def file(self) -> Path:
        return self.folder / SKILL_FILENAME


class SkillError(NarError):
    """A ``SKILL.md`` that cannot be used; carries the file and the reason.

    ``message`` is for the user, in the interface language. ``model_message`` is the same
    text in English: the ``skill`` tool returns it to the model.
    """

    def __init__(self, file: Path, reason_key: str, **values: object) -> None:
        super().__init__(f"{file}: {t(reason_key, **values)}")
        self.file = file
        self.reason = t(reason_key, **values)
        self.model_message = f"{file}: {t_en(reason_key, **values)}"


def user_skills_dir() -> Path:
    return nar_home() / "skills"


def project_skills_dir(cwd: Path) -> Path:
    return cwd / ".nar" / "skills"


def parse_skill(folder: Path, origin: Origin) -> Skill:
    """Read ``folder/SKILL.md`` and check every rule; raise :class:`SkillError` if one fails."""
    file = folder / SKILL_FILENAME
    meta, _ = _read(file)
    name = _text_field(file, meta, "name")
    description = _text_field(file, meta, "description")
    if len(name) > MAX_NAME_LENGTH or not NAME_PATTERN.fullmatch(name):
        raise SkillError(file, "skill.error.invalid_name", name=repr(name), max=MAX_NAME_LENGTH)
    if name != folder.name:
        raise SkillError(
            file, "skill.error.name_mismatch", name=repr(name), folder=repr(folder.name)
        )
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise SkillError(
            file,
            "skill.error.description_long",
            count=len(description),
            max=MAX_DESCRIPTION_LENGTH,
        )
    return Skill(name=name, description=description, folder=folder, origin=origin)


def read_instructions(skill: Skill) -> str:
    """Re-read the file and return the text after the frontmatter, stripped."""
    _, body = _read(skill.file)
    return body.strip()


def discover_skills(cwd: Path, warn: Callable[[str], None]) -> tuple[Skill, ...]:
    """Skills from the user folder and the project folder, sorted by name. Never raises.

    A project skill replaces a user skill with the same name. Each broken skill gives one
    warning and is skipped.
    """
    found: dict[str, Skill] = {}
    for directory, origin in (
        (user_skills_dir(), "user"),
        (project_skills_dir(cwd), "project"),
    ):
        for skill in _scan(directory, origin, warn):
            found[skill.name] = skill
    return tuple(sorted(found.values(), key=lambda skill: skill.name))


def _scan(directory: Path, origin: Origin, warn: Callable[[str], None]) -> list[Skill]:
    try:
        folders = sorted(entry for entry in directory.iterdir() if entry.is_dir())
    except OSError:
        return []  # a missing or unreadable skills folder is not worth a warning
    skills = []
    for folder in folders:
        try:
            if not (folder / SKILL_FILENAME).is_file():
                continue
            skills.append(parse_skill(folder, origin))
        except SkillError as exc:
            warn(t("skill.skipped", prefix=t("warning.prefix"), error=exc.message))
        except OSError as exc:
            path = folder / SKILL_FILENAME
            reason = exc.strerror or exc
            warn(
                t("skill.skipped_unreadable", prefix=t("warning.prefix"), path=path, reason=reason)
            )
    return skills


def _read(file: Path) -> tuple[dict[str, object], str]:
    try:
        text = file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise SkillError(file, "skill.error.not_utf8") from None
    except OSError as exc:
        raise SkillError(file, "skill.error.cannot_read", reason=exc.strerror or exc) from None
    return _split(file, text)


def _split(file: Path, text: str) -> tuple[dict[str, object], str]:
    lines = text.removeprefix("\ufeff").splitlines()
    if not lines or lines[0].strip() != DELIMITER:
        raise SkillError(file, "skill.error.frontmatter_missing", delimiter=DELIMITER)
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == DELIMITER), None)
    if end is None:
        raise SkillError(file, "skill.error.frontmatter_unclosed", delimiter=DELIMITER)
    try:
        meta = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError:
        raise SkillError(file, "skill.error.frontmatter_yaml") from None
    if not isinstance(meta, dict):
        raise SkillError(file, "skill.error.frontmatter_mapping")
    return meta, "\n".join(lines[end + 1 :])


def _text_field(file: Path, meta: dict[str, object], key: str) -> str:
    value = meta.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise SkillError(file, "skill.error.no_field", field=key)
    if not isinstance(value, str):
        raise SkillError(file, "skill.error.not_string", field=key)
    return value.strip()
