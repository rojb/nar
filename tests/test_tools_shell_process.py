from nar.tools.shell_process import (
    POWERSHELL_PREFIX,
    POWERSHELL_SUFFIX,
    ShellSpec,
    StreamCapture,
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


# --- StreamCapture ------------------------------------------------------------


def feed_in_chunks(capture, data, size):
    for start in range(0, len(data), size):
        capture.feed(data[start : start + size])


def test_small_input_comes_back_whole_without_a_marker():
    capture = StreamCapture()
    data = bytes(range(65, 65 + 25)) * 4  # 100 bytes
    feed_in_chunks(capture, data, 7)

    assert capture.text() == data.decode()
    assert capture.total == 100
    assert "omitted" not in capture.text()


def test_large_input_keeps_head_and_tail_and_counts_the_rest():
    capture = StreamCapture()
    data = b"".join(f"{n:09d}\n".encode() for n in range(10_000))  # 100,000 bytes
    for start in range(0, len(data), 1_000):
        capture.feed(data[start : start + 1_000])
        assert len(capture.head) + len(capture.tail) <= 14_000

    text = capture.text()
    assert text.startswith("000000000\n")
    assert text.endswith("000009999\n")
    assert f"[... {100_000 - 14_000} bytes omitted ...]" in text
    assert capture.total == 100_000


def test_memory_stays_bounded_even_for_a_single_huge_chunk():
    capture = StreamCapture()
    capture.feed(b"x" * 1_000_000)

    assert len(capture.head) == 4_000
    assert len(capture.tail) == 10_000
    assert capture.omitted == 1_000_000 - 14_000


def test_lines_counts_a_final_line_without_newline():
    with_newline, without_newline, empty = StreamCapture(), StreamCapture(), StreamCapture()
    with_newline.feed(b"a\nb\nc\n")
    without_newline.feed(b"a\nb\nc")

    assert with_newline.lines == 3
    assert without_newline.lines == 3
    assert empty.lines == 0
    assert empty.text() == ""


def test_lines_cover_the_omitted_part_too():
    capture = StreamCapture()
    feed_in_chunks(capture, b"x\n" * 50_000, 4_096)

    assert capture.lines == 50_000


def test_multibyte_character_split_at_the_head_boundary_gives_one_replacement():
    capture = StreamCapture()
    capture.feed(b"a" * 3_999 + "€".encode() + b"b" * 20_000)  # euro sign: 3 bytes

    text = capture.text()

    assert text.count("�") == 1
    assert "bytes omitted" in text


def test_multibyte_character_across_chunks_is_decoded_whole_when_nothing_is_omitted():
    capture = StreamCapture()
    encoded = "café €".encode()
    for byte in encoded:
        capture.feed(bytes([byte]))

    assert capture.text() == "café €"


def test_invalid_byte_becomes_the_replacement_character():
    capture = StreamCapture()
    capture.feed(b"a\xffb")

    assert capture.text() == "a�b"


def test_crlf_becomes_lf():
    capture = StreamCapture()
    capture.feed(b"a\r\nb")

    assert capture.text() == "a\nb"


def test_drain_reads_until_end_of_file():
    class Pipe:
        def __init__(self, chunks):
            self.chunks = list(chunks)

        def read1(self, size):
            assert size == 65_536
            return self.chunks.pop(0) if self.chunks else b""

    capture = StreamCapture()
    capture.drain(Pipe([b"ab", b"cd\n", b"ef"]))

    assert capture.text() == "abcd\nef"
    assert capture.lines == 2
