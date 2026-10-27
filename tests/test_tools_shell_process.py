from nar.tools.shell_process import (
    POWERSHELL_PREFIX,
    POWERSHELL_SUFFIX,
    ShellSpec,
    build_argv,
    find_shell,
)


def fake_which(*available):
    return lambda program: f"/fake/{program}" if program in available else None


# --- find_shell ---------------------------------------------------------------


def test_windows_prefers_pwsh_over_windows_powershell():
    shell = find_shell(windows=True, which=fake_which("pwsh", "powershell"))
    assert shell.executable == "/fake/pwsh"
    assert shell.powershell


def test_windows_falls_back_to_windows_powershell():
    shell = find_shell(windows=True, which=fake_which("powershell"))
    assert shell.executable == "/fake/powershell"
    assert shell.powershell


def test_windows_without_powershell_finds_nothing():
    assert find_shell(windows=True, which=fake_which()) is None


def test_windows_never_picks_bash():
    assert find_shell(windows=True, which=fake_which("bash", "sh")) is None


def test_posix_prefers_bash():
    shell = find_shell(windows=False, which=fake_which("bash", "sh"))
    assert shell.executable == "/fake/bash"
    assert not shell.powershell


def test_posix_falls_back_to_sh():
    shell = find_shell(windows=False, which=fake_which("sh"))
    assert shell.executable == "/fake/sh"


def test_posix_without_a_shell_finds_nothing():
    assert find_shell(windows=False, which=fake_which("pwsh", "powershell")) is None


def test_shell_names_identify_the_shell():
    assert find_shell(windows=True, which=fake_which("pwsh")).name == "PowerShell 7"
    assert find_shell(windows=True, which=fake_which("powershell")).name == "Windows PowerShell"
    assert find_shell(windows=False, which=fake_which("bash")).name == "bash"
    assert find_shell(windows=False, which=fake_which("sh")).name == "sh"


# --- build_argv ---------------------------------------------------------------


def test_powershell_argv_has_flags_and_the_wrapped_command():
    shell = ShellSpec(name="Windows PowerShell", executable="ps.exe", powershell=True)
    command = "python --version  # keep me"

    argv = build_argv(shell, command)

    assert argv[0] == "ps.exe"
    assert argv[1:4] == ["-NoProfile", "-NonInteractive", "-Command"]
    script = argv[4]
    assert script.startswith(POWERSHELL_PREFIX)
    assert script.endswith(POWERSHELL_SUFFIX)
    assert command in script.splitlines()
    assert script == POWERSHELL_PREFIX + command + POWERSHELL_SUFFIX


def test_posix_argv_is_exactly_executable_dash_c_command():
    shell = ShellSpec(name="bash", executable="/bin/bash", powershell=False)
    assert build_argv(shell, "ls -la") == ["/bin/bash", "-c", "ls -la"]
