"""Errors, warnings and notes raised by the modules, in Spanish (RRG-98)."""

import pytest

from nar.cli import main
from nar.i18n import set_language
from nar.session import SessionFileError, SessionNotFound, SessionStore, sessions_dir
from test_cli import _Runner


@pytest.fixture
def runner():
    return _Runner()


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NAR_HOME", str(tmp_path / "nar-home"))
    return tmp_path


# --- SessionNotFound (4.1) ---------------------------------------------------------------------


def test_missing_and_invalid_ids_raise_session_not_found(tmp_path):
    store = SessionStore(tmp_path)
    for call in (lambda: store.load("zzz"), lambda: store.find("zzz"), lambda: store.find("a b")):
        with pytest.raises(SessionNotFound):
            call()


def test_a_corrupt_file_is_a_session_error_but_not_session_not_found(tmp_path):
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    with pytest.raises(SessionFileError) as caught:
        SessionStore(tmp_path).load("bad")
    assert not isinstance(caught.value, SessionNotFound)


def test_resume_of_an_unknown_id_adds_the_hint_in_spanish(runner, workdir):
    result = runner.invoke(main, ["--resume", "zzz", "--language", "es"])
    assert result.exit_code == 1
    assert "zzz" in result.stderr
    assert 'nar --resume <id> "solicitud"' in result.stderr
    assert "to give a prompt" not in result.stderr


def test_resume_of_a_corrupt_file_gets_no_hint_in_spanish(runner, workdir):
    sessions_dir().mkdir(parents=True)
    (sessions_dir() / "broken.json").write_text("not json", encoding="utf-8")
    result = runner.invoke(main, ["--resume", "broken", "--language", "es"])
    assert result.exit_code == 1
    assert "broken.json" in result.stderr
    assert "solicitud" not in result.stderr


def test_resume_without_saved_sessions_is_spanish(runner, workdir):
    result = runner.invoke(main, ["--resume", "--language", "es"])
    assert result.exit_code == 1
    assert "no hay ninguna sesión guardada en" in result.stderr
    assert "usa nar --resume <id>" in result.stderr
    set_language("en")


# --- config, session, skill and instruction errors (4.2) ------------------------------------------


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_an_invalid_language_in_a_file_is_one_spanish_line_that_lists_the_values(runner, workdir):
    _write(workdir / ".nar" / "config.toml", 'language = "fr"\n')
    set_language("es")
    result = runner.invoke(main, ["--show-config"], interface=None)
    assert result.exit_code == 1
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: valor no válido \"fr\" para el ajuste 'language' en ")
    assert lines[0].endswith("valores permitidos: es, en")


def test_config_errors_keep_the_key_names_and_values_in_spanish(workdir):
    from nar.config import load_config
    from nar.errors import ConfigError

    set_language("es")
    _write(workdir / ".nar" / "config.toml", 'bogus = 1\napproval = "maybe"\n')
    with pytest.raises(ConfigError) as caught:
        load_config(workdir)
    assert "ajuste desconocido 'bogus' en " in caught.value.message
    assert "valor no válido \"maybe\" para el ajuste 'approval'" in caught.value.message


def test_a_default_value_is_reported_against_the_built_in_defaults(workdir):
    from nar.config import load_config
    from nar.errors import ConfigError

    set_language("es")
    _write(workdir / ".nar" / "config.toml", "[mcp_servers.x]\n")
    with pytest.raises(ConfigError) as caught:
        load_config(workdir)
    assert "ajuste" in caught.value.message
    _write(workdir / ".nar" / "config.toml", "not = = toml\n")
    with pytest.raises(ConfigError) as caught:
        load_config(workdir)
    assert caught.value.message.startswith("TOML no válido en ")
    assert "(línea 1)" in caught.value.message


def test_the_source_words_follow_the_language_and_the_file_paths_do_not(workdir):
    from nar.config import load_config

    _write(workdir / ".nar" / "config.toml", 'approval = "yolo"\n')
    loaded = load_config(workdir, {"model": "m1"})
    set_language("es")
    lines = loaded.describe(workdir)
    assert "language = es  (predeterminado)" in lines
    assert "approval = yolo  (.nar/config.toml)" in lines
    assert "model = m1  (línea de comandos)" in lines
    assert loaded.source_of("language", workdir) == "predeterminado"
    set_language("en")
    lines = loaded.describe(workdir)
    assert "language = es  (default)" in lines
    assert "model = m1  (command line)" in lines


def test_session_store_errors_are_spanish(tmp_path):
    set_language("es")
    store = SessionStore(tmp_path)
    for call, text in (
        (lambda: store.load("zzz"), "no hay ninguna sesión 'zzz'"),
        (lambda: store.find("a b"), "id de sesión no válido 'a b'"),
    ):
        with pytest.raises(SessionNotFound) as caught:
            call()
        assert caught.value.message == text
    (tmp_path / "ab1.json").write_text("{}", encoding="utf-8")
    (tmp_path / "ab2.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SessionFileError, match="'ab' coincide con varias sesiones: ab1, ab2"):
        store.find("ab")
    with pytest.raises(SessionFileError, match="no es una sesión válida"):
        store.load("ab1")
    (tmp_path / "bad.json").write_text("nope", encoding="utf-8")
    with pytest.raises(SessionFileError, match="no se puede leer el archivo de sesión"):
        store.load("bad")


def test_checkpoint_errors_are_spanish(tmp_path):
    from nar.providers.messages import Message
    from nar.session.checkpoints import CheckpointStore

    set_language("es")
    store = CheckpointStore(tmp_path)
    messages = [Message("user", "hi")]
    store.add("s1", "base", messages)
    for name, text in (
        ("base", "ya hay un punto de control llamado 'base'"),
        ("12", "no puede ser solo dígitos"),
        ("x" * 61, "como máximo 60 caracteres"),
    ):
        with pytest.raises(SessionFileError, match=text):
            store.add("s1", name, messages)
    with pytest.raises(SessionFileError, match="no hay ningún punto de control 'zz'"):
        store.find("s1", "zz")


def test_recorder_warning_uses_the_aviso_prefix(tmp_path):
    from nar.context.manager import ContextManager
    from nar.session.recorder import SessionRecorder
    from nar.session.store import Session

    class Failing(SessionStore):
        def save(self, session):
            raise OSError(13, "Permission denied")

    set_language("es")
    warnings = []
    context = ContextManager("system")
    context.add_user("hi")
    recorder = SessionRecorder(
        Failing(tmp_path), Session.new(tmp_path, "fake", "m"), context, warnings.append
    )
    recorder.save()
    assert warnings == ["aviso: no se pudo guardar la sesión: Permission denied"]


def _skill(folder, name, body="---\nname: {n}\ndescription: d\n---\nbody\n"):
    _write(folder / name / "SKILL.md", body.format(n=name))


def test_a_broken_skill_warns_with_aviso_and_the_model_still_gets_english(workdir, monkeypatch):
    from nar.skills import SkillError, discover_skills, parse_skill
    from nar.skills.loader import project_skills_dir

    folder = project_skills_dir(workdir)
    _skill(folder, "good")
    _write(folder / "bad" / "SKILL.md", "no frontmatter\n")
    set_language("es")
    warnings = []
    skills = discover_skills(workdir, warnings.append)
    assert [s.name for s in skills] == ["good"]
    assert len(warnings) == 1
    assert warnings[0].startswith("aviso: se omite un skill: ")
    assert "falta el encabezado (el archivo debe empezar con ---)" in warnings[0]

    with pytest.raises(SkillError) as caught:
        parse_skill(folder / "bad", "project")
    assert caught.value.model_message.endswith(
        "the frontmatter is missing (the file must start with ---)"
    )
    assert "encabezado" in caught.value.message


def test_the_skill_tool_returns_the_english_reason_in_spanish_mode(workdir):
    from nar.skills import Skill
    from nar.tools.base import ToolContext
    from nar.tools.skill import SkillTool

    folder = workdir / "skills" / "gone"
    folder.mkdir(parents=True)
    skill = Skill(name="gone", description="d", folder=folder, origin="project")
    set_language("es")
    result = SkillTool([skill]).execute({"name": "gone"}, ToolContext(cwd=workdir))
    assert result.is_error
    assert "cannot load the skill 'gone'" in result.output
    assert "cannot read the file" in result.output or "frontmatter" in result.output
    assert "encabezado" not in result.output


def test_project_instruction_warnings_use_the_aviso_prefix(workdir, capsys):
    from nar.context import load_project_instructions

    (workdir / "AGENTS.md").write_bytes(b"\xff\xfe\x00bad")
    set_language("es")
    assert load_project_instructions(workdir) is None
    err = capsys.readouterr().err
    assert "aviso: " in err
    assert "no es UTF-8 válido; se continúa sin instrucciones del proyecto" in err
    (workdir / "AGENTS.md").write_text("x" * (65 * 1024), encoding="utf-8")
    load_project_instructions(workdir)
    assert "pesa más de 64 KB; solo se usan los primeros 64 KB" in capsys.readouterr().err
