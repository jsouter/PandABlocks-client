from collections import deque
from unittest.mock import patch

import h5py
import pytest

from pandablocks import cli


def test_writing_fast_hdf(dummy_server_in_thread, fast_dump, tmp_path):
    dummy_server_in_thread.data = fast_dump
    cli.main(["hdf", "localhost", str(tmp_path / "%d.h5")])
    hdf_file = h5py.File(tmp_path / "1.h5", "r")
    assert hdf_file["/COUNTER1.OUT.Min"][:] == pytest.approx(range(1, 59))


class MockInput:
    def __init__(self, *commands: str):
        self._commands = deque(commands)

    def __call__(self, prompt):
        assert prompt == "> "
        try:
            return self._commands.popleft()
        except IndexError:
            raise EOFError()


def test_interactive_simple(dummy_server_in_thread, capsys):
    mock_input = MockInput("PCAP.ACTIVE?")
    dummy_server_in_thread.send.append("OK =0")
    with patch("pandablocks.control.input", side_effect=mock_input):
        cli.main(["control", "localhost", "--no-readline"])
    captured = capsys.readouterr()
    assert captured.out == "OK =0\n\n"
    assert captured.err == ""
