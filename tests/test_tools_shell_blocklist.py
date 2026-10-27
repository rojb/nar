import pytest

from nar.tools.shell_blocklist import find_blocked

BLOCKED = [
    "rm -rf /",
    "RM   -fr   ~/",
    "sudo rm -rf /*",
    "rm -r -f /",
    "rm --recursive --force /",
    "rm -rf $HOME",
    "rm -rf '/'",
    r"Remove-Item -Recurse -Force C:\ ",
    r"Remove-Item C:\ -Recurse -Force",
    r"ri -r -fo C:\ ",
    r"Remove-Item -Recurse -Force $env:SystemRoot",
    r"del /s /q C:\ ",
    r"del /f /s /q C:\*.*",
    r"rd /s /q C:\ ",
    r"rmdir /s /q c:\*",
    "format C:",
    "format c: /q",
    "Format-Volume -DriveLetter D",
    "mkfs.ext4 /dev/sda1",
    "dd if=/dev/zero of=/dev/sda",
    "echo x > /dev/sda",
    ":(){ :|:& };:",
    "shutdown /s /t 0",
    "sudo reboot",
    "Stop-Computer",
    "Restart-Computer -Force",
    "ls && shutdown -h now",
    "(shutdown -h now)",
    "echo done; rm -rf /",
    "echo hi\nrm -rf /",
    "echo hi\r\nrm -rf /\r\n",
]

ALLOWED = [
    "rm -rf build/",
    "rm -rf ./node_modules",
    "rm -rf /tmp/x",
    "rm -rf ~/proj/build",
    "rm -f /tmp/a",
    r"Remove-Item -Recurse -Force .\build",
    r"Remove-Item -Recurse -Force C:\Users\me\proj\build",
    r"del /s /q C:\proj\tmp\*.log",
    "rmdir /s /q build",
    r"Get-ChildItem C:\ -Recurse",
    "Format-Table",
    "dd if=a of=b",
    'echo "do not shutdown"',
    "git commit -m 'fix shutdown hook'",
    "git log --format=%H",
    "cat reboot.txt",
    "python --version",
    "pytest -q",
    "rm -rf build/ && ls /",
]


@pytest.mark.parametrize("command", BLOCKED)
def test_dangerous_commands_are_blocked_with_a_reason(command):
    reason = find_blocked(command)
    assert isinstance(reason, str)
    assert reason.strip()


@pytest.mark.parametrize("command", ALLOWED)
def test_ordinary_commands_are_not_blocked(command):
    assert find_blocked(command) is None


def test_matching_ignores_case_tabs_and_repeated_spaces():
    assert find_blocked("RM\t-RF\t\t/") is not None


def test_quotes_cannot_hide_the_target():
    assert find_blocked('rm -rf "/"') is not None


def test_reasons_describe_the_matched_rule():
    assert "root" in find_blocked("rm -rf /")
    assert "format" in find_blocked("format C:")
    assert "shutting down" in find_blocked("shutdown -h now")
