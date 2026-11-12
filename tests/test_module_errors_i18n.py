"""Errors, warnings and notes raised by the modules, in Spanish (RRG-98)."""

import pytest

from nar.cli import main
from nar.i18n import set_language
from nar.session import SessionFileError, SessionNotFound, SessionStore, sessions_dir
from nar.tools.base import ToolResult
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


# --- warnings and notes of MCP, hooks, custom tools, compaction and retries (4.3) -----------------


def test_mcp_manager_warnings_and_note_are_spanish(tmp_path, monkeypatch):
    from nar.config import McpServerConfig
    from nar.mcp.manager import McpManager

    monkeypatch.setenv("NAR_HOME", str(tmp_path / "nar-home"))
    set_language("es")
    notes, warnings = [], []
    manager = McpManager.connect(
        {"x": McpServerConfig(command="does-not-exist")}, {}, notes.append, warnings.append
    )
    manager.close()
    assert notes == ["conectando servidores MCP: x ..."]
    assert warnings == [
        "aviso: el servidor MCP 'x' no está disponible: command 'does-not-exist' not found"
    ]


def test_mcp_tool_warnings_are_spanish(tmp_path):
    from test_mcp_tool import FakeConnection, make_manager, register, spec

    set_language("es")
    manager = make_manager(
        {"s": ([spec("a"), spec("b")], FakeConnection(), {"enabled_tools": ["a", "zz"]})}
    )
    _, warnings = register(manager, tmp_path)
    assert warnings == [
        "aviso: el servidor MCP 's' no tiene la herramienta 'zz' (revisa enabled_tools)"
    ]


def _hook_runner(tmp_path, hooks, **kwargs):
    from test_hooks_runner import runner

    return runner(hooks, tmp_path, **kwargs)


def test_hook_warnings_and_notes_are_spanish_and_the_refusal_stays_english(tmp_path):
    from nar.config.models import HookEvent
    from nar.providers.messages import ToolCall

    set_language("es")
    notes, warns = [], []
    hooks = _hook_runner(
        tmp_path,
        {HookEvent.BEFORE_TOOL: ["import sys; print('no way'); sys.exit(2)"]},
        notes=notes,
        warns=warns,
    )
    result = hooks.before_tool(ToolCall("c1", "edit_file", {"path": "a.py"}))
    assert result.output == "the before_tool hook refused this call: no way"
    assert warns == []

    hooks = _hook_runner(
        tmp_path, {HookEvent.BEFORE_TOOL: ["import sys; sys.exit(1)"]}, notes=notes, warns=warns
    )
    assert hooks.before_tool(ToolCall("c1", "edit_file", {})) is None
    assert warns[-1].startswith("aviso: el hook before_tool falló con el código de salida 1 y no")
    assert "bloqueó la llamada: " in warns[-1]

    hooks = _hook_runner(
        tmp_path, {HookEvent.AFTER_TOOL: ["import sys; sys.exit(1)"]}, notes=notes, warns=warns
    )
    hooks.after_tool(ToolCall("c1", "echo", {}), ToolResult("ok"))
    assert warns[-1].startswith("aviso: el hook after_tool falló con el código de salida 1: ")

    hooks = _hook_runner(
        tmp_path,
        {HookEvent.BEFORE_TOOL: ["import time; time.sleep(30)"]},
        timeout=0.3,
        warns=warns,
    )
    hooks.before_tool(ToolCall("c1", "edit_file", {}))
    assert warns[-1].startswith(
        "aviso: el hook before_tool superó el tiempo de espera de 0.3 s y no bloqueó la llamada: "
    )
    hooks = _hook_runner(tmp_path, {HookEvent.BEFORE_AGENT: ["print('hi')"]}, notes=notes)
    hooks.before_agent("p")
    assert any(note.startswith("hook before_agent: ") for note in notes)


def test_a_hook_that_cannot_run_without_a_shell_warns_in_spanish(tmp_path):
    from nar.config.models import HookEvent
    from nar.hooks import HookRunner

    set_language("es")
    warns = []
    hooks = HookRunner({HookEvent.AFTER_AGENT: ["x"]}, tmp_path, warn=warns.append)
    hooks._shell, hooks._shell_resolved = None, True
    hooks.after_agent("p", "done", 1)
    assert warns == [
        "aviso: el hook after_agent no se pudo ejecutar: no se encontró ningún shell en este "
        "sistema: x"
    ]


def test_custom_tool_messages_are_spanish_and_count_correctly(tmp_path, monkeypatch):
    from nar.tools import ToolContext, ToolRegistry
    from nar.tools.custom import load_custom_tools

    class Cannot:
        def available(self):
            return False

        def ask(self, folder, files):
            return False

    monkeypatch.setenv("NAR_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    tools = project / ".nar" / "tools"
    tools.mkdir(parents=True)
    (tools / "a.py").write_text("x = 1\n", encoding="utf-8")

    def load():
        warnings = []
        registry = ToolRegistry(ToolContext(project))
        load_custom_tools(registry, project, Cannot(), False, print, warnings.append)
        return warnings

    set_language("es")
    (warning,) = load()
    assert warning.startswith("aviso: no se ejecuta el archivo de herramientas de ")
    assert warning.endswith("usa --trust-project-tools para permitirlo")
    (tools / "b.py").write_text("x = 2\n", encoding="utf-8")
    assert load()[0].startswith("aviso: no se ejecutan los 2 archivos de herramientas de ")
    set_language("en")
    assert load()[0].startswith("warning: not running the 2 tool files in ")


def test_custom_tool_file_problems_and_notes_are_spanish(tmp_path, monkeypatch):
    from nar.tools import ToolContext, ToolRegistry
    from nar.tools.custom import load_custom_tools
    from test_tools_custom import Question, good

    monkeypatch.setenv("NAR_HOME", str(tmp_path / "home"))
    tools = tmp_path / "home" / "tools"
    tools.mkdir(parents=True)
    (tools / "ok.py").write_text(good(), encoding="utf-8")
    (tools / "bad.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    set_language("es")
    notes, warnings = [], []
    project = tmp_path / "project"
    project.mkdir()
    registry = ToolRegistry(ToolContext(project))
    load_custom_tools(registry, project, Question(), False, notes.append, warnings.append)
    assert any(note.startswith("herramienta propia cargada: custom_one (") for note in notes)
    assert any(w.startswith("aviso: archivo de herramienta ") and "boom" in w for w in warnings)


def test_compaction_errors_are_spanish():
    from nar.context.compaction import CompactionError
    from nar.context.manager import ContextManager
    from nar.providers.messages import Message
    from test_context_compaction import SummaryProvider, compactor, conversation, long_session

    set_language("es")
    cases = [
        (ContextManager("system"), SummaryProvider(), "no hay nada que compactar"),
        (
            conversation(*long_session()),
            SummaryProvider(""),
            "el modelo devolvió un resumen vacío",
        ),
        (
            conversation(Message.user("hello"), Message.assistant("x" * 40)),
            SummaryProvider("s" * 500),
            "el resumen no haría más pequeña la conversación",
        ),
    ]
    for context, provider, text in cases:
        with pytest.raises(CompactionError) as caught:
            compactor(provider).compact(context)
        assert caught.value.message == text


def test_retry_notice_is_spanish_and_its_reason_stays_available_in_english():
    from nar.providers.retry import RetryNotice, RetryPolicy, stream_with_retries
    from test_retry import CONTEXT, DONE, Script, connection_error, status_error

    set_language("es")
    notices = []
    script = Script(status_error(503), connection_error(), DONE)
    policy = RetryPolicy(sleep=lambda seconds: None)
    list(stream_with_retries(script, CONTEXT, policy, notices.append))
    assert [n.reason for n in notices] == ["server error 503", "connection error"]
    assert str(notices[0]) == (
        f"reintentando openrouter en {notices[0].delay:.1f}s (error del servidor 503, intento 2/4)"
    )
    assert "(error de conexión, intento 3/4)" in str(notices[1])
    set_language("en")
    assert str(notices[0]) == (
        f"retrying openrouter in {notices[0].delay:.1f}s (server error 503, attempt 2/4)"
    )
    assert isinstance(notices[0], RetryNotice)


# --- preview titles and summaries of edit_file, write_file and shell (4.4) -----------------------


def _in_both_languages(call):
    """The value ``call()`` gives in Spanish and then in English."""
    set_language("es")
    spanish = call()
    set_language("en")
    return spanish, call()


def test_edit_file_title_and_summary_are_spanish_and_the_model_result_is_not(tmp_path):
    from nar.tools import ToolContext
    from nar.tools.edit_file import EditFileTool

    (tmp_path / "a.py").write_text("one\ntwo\n", encoding="utf-8")
    context = ToolContext(cwd=tmp_path)
    arguments = {"path": "a.py", "old_string": "two", "new_string": "2"}
    tool = EditFileTool()

    set_language("es")
    assert tool.preview(arguments, context).title == "Editar a.py (+1 -1)"
    failing = {"path": "a.py", "old_string": "zzz", "new_string": "2"}
    shown = tool.preview(failing, context)
    assert shown.title == "Editar a.py"
    assert shown.error.startswith("old_string was not found in ")  # reaches the model
    spanish = tool.execute(arguments, context)
    (tmp_path / "a.py").write_text("one\ntwo\n", encoding="utf-8")
    set_language("en")
    english = tool.execute(arguments, context)
    assert spanish.summary == "editado a.py (+1 -1)"
    assert english.summary == "edited a.py (+1 -1)"
    assert spanish.output == english.output


def test_write_file_titles_and_summaries_are_spanish_with_plurals(tmp_path):
    from nar.tools import ToolContext
    from nar.tools.write_file import WriteFileTool

    context = ToolContext(cwd=tmp_path)
    tool = WriteFileTool()
    one = {"path": "a.txt", "content": "x\n"}
    many = {"path": "b.txt", "content": "x\ny\n"}

    set_language("es")
    assert tool.preview(one, context).title == "Crear a.txt (1 línea)"
    assert tool.preview(many, context).title == "Crear b.txt (2 líneas)"
    assert tool.execute(one, context).summary == "creado a.txt (1 línea)"
    assert tool.preview(one, context).title == "Sobrescribir a.txt (sin cambios)"
    assert tool.preview({"path": "a.txt", "content": "y\n"}, context).title == (
        "Sobrescribir a.txt"
    )
    assert tool.execute(many, context).summary == "creado b.txt (2 líneas)"
    assert tool.execute({"path": "b.txt", "content": "z\n"}, context).summary == (
        "sobrescrito b.txt (1 línea)"
    )
    bad = {"path": str(tmp_path), "content": "x"}
    shown = tool.preview(bad, context)
    assert shown.title.startswith("Escribir ")
    assert shown.error.startswith("path is a directory: ")  # reaches the model

    (tmp_path / "c.bin").write_bytes(b"\x00\x01")
    binary = tool.preview({"path": "c.bin", "content": "t\n"}, context)
    assert binary.detail.startswith(
        "el archivo existente no se puede mostrar como texto; contenido nuevo: 1 línea, 2 bytes"
    )

    set_language("en")
    assert tool.execute(many, context).output.startswith("overwritten ")


def test_write_file_model_output_is_the_same_in_both_languages(tmp_path):
    from nar.tools import ToolContext
    from nar.tools.write_file import WriteFileTool

    context = ToolContext(cwd=tmp_path)
    arguments = {"path": "same.txt", "content": "a\nb\n"}

    def create():
        (tmp_path / "same.txt").unlink(missing_ok=True)
        return WriteFileTool().execute(arguments, context)

    spanish, english = _in_both_languages(create)
    assert spanish.output == english.output
    assert spanish.output.startswith("created ")
    assert spanish.summary == "creado same.txt (2 líneas)"
    assert english.summary == "created same.txt (2 lines)"


def test_shell_title_and_summaries_are_spanish_and_the_model_result_is_not(tmp_path):
    import sys

    from nar.tools import ToolContext
    from nar.tools.shell import ShellTool
    from nar.tools.shell_process import find_shell

    if find_shell() is None:
        pytest.skip("no shell installed")
    tool = ShellTool()
    context = ToolContext(cwd=tmp_path)
    script = tmp_path / "s.py"
    script.write_text("print('a')\nprint('b')\n", encoding="utf-8")
    shell = find_shell()
    command = (
        f"& '{sys.executable}' '{script}'" if shell.powershell else f"'{sys.executable}' '{script}'"
    )

    set_language("es")
    assert tool.preview({"command": command, "timeout": 7}, context).title == (
        f"Ejecutar en {shell.name} (límite 7 s)"
    )
    assert tool.preview({"command": ""}, context).title == "Ejecutar comando"
    spanish = tool.execute({"command": command}, context)
    set_language("en")
    english = tool.execute({"command": command}, context)
    assert spanish.summary == "salida 0 · 2 líneas"
    assert english.summary == "exit 0 · 2 lines"
    assert spanish.output == english.output
    assert spanish.output.startswith("exit code: 0")  # model-bound text stays English


def test_shell_summaries_for_no_output_and_timeout_are_spanish():
    from types import SimpleNamespace

    from nar.tools.shell import _result

    def capture(lines):
        return SimpleNamespace(total=lines, lines=lines, text=lambda: "x\n" * lines)

    quiet = SimpleNamespace(exit_code=3, stdout=capture(0), stderr=capture(0), timed_out=False)
    slow = SimpleNamespace(exit_code=None, stdout=capture(1), stderr=capture(0), timed_out=True)
    set_language("es")
    assert _result(quiet, 5).summary == "salida 3 · sin salida"
    assert _result(slow, 5).summary == "se agotó el tiempo tras 5 s · 1 línea"
    assert _result(slow, 5).output.startswith("command timed out after 5 seconds")
    set_language("en")
    assert _result(quiet, 5).summary == "exit 3 · no output"
    assert _result(slow, 5).summary == "timed out after 5 s · 1 line"
