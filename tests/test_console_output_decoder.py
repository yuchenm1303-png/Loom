"""Pipe output from Windows console tools is in the OEM code page; the agent must read it as the text it was."""

from __future__ import annotations

import os
import subprocess

import pytest

from app.agent_runtime.process_runtime import ConsoleOutputDecoder, _console_code_page


def feed(decoder: ConsoleOutputDecoder, chunks: list[bytes]) -> str:
    return "".join(decoder.decode(chunk) for chunk in chunks) + decoder.decode(b"", final=True)


def test_utf8_output_stays_utf8_even_split_mid_character() -> None:
    data = "C:\\Users\\邹羽宸\\Downloads 675 MB\n".encode("utf-8")
    # Every split point, including inside a three-byte character: a cut sequence is held, not a failure.
    for cut in range(1, len(data)):
        assert feed(ConsoleOutputDecoder("cp936"), [data[:cut], data[cut:]]) == data.decode("utf-8")


def test_code_page_output_switches_once_at_the_first_non_utf8_byte() -> None:
    text = "UsedGB=176.59\r\n       675 MB  C:\\Users\\邹羽宸\\Downloads\r\n"
    data = text.encode("cp936")
    for cut in range(1, len(data)):
        assert feed(ConsoleOutputDecoder("cp936"), [data[:cut], data[cut:]]) == text


def test_without_a_code_page_it_is_utf8_with_replacement() -> None:
    assert feed(ConsoleOutputDecoder(None), [b"ok \xff\n"]) == "ok \ufffd\n"


@pytest.mark.skipif(os.name != "nt" or _console_code_page() is None, reason="needs Windows with a non-UTF-8 console code page")
def test_powershell_pipe_output_reads_back_as_written() -> None:
    name = "邹羽宸"
    process = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", f"Write-Output 'C:\\Users\\{name}\\Downloads'"],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
    )
    assert feed(ConsoleOutputDecoder(_console_code_page()), [process.stdout]).strip() == f"C:\\Users\\{name}\\Downloads"
