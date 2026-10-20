from __future__ import annotations

import asyncio
import json

import pytest

from browser_to_mcp.cli import run
from browser_to_mcp.recorder.capture import Capture
from browser_to_mcp.recorder.command import record, run_record
from browser_to_mcp.recorder.draft import RecordingError


class FakeSession:
    incomplete = False
    closed_early = False
    instances = []

    def __init__(self, url, domains, **kwargs):
        self.capture = Capture(domains)
        self.ended = asyncio.Event()
        self.closed = False
        if self.incomplete:
            self.capture.diagnose("frame")
        if self.closed_early:
            self.capture.diagnose("browser_closed")
            self.ended.set()
        self.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def close(self):
        self.closed = True

    async def finish(self, *, partial=False):
        await self.close()
        return self.capture.draft(partial=partial)


@pytest.mark.parametrize(
    "commands,incomplete,closed_early,saved,status",
    [
        (["stop"], False, False, True, "captured"),
        (["cancel"], False, False, False, "cancelled"),
        (["save-partial"], False, False, True, "partial"),
        (["stop", "cancel"], True, False, False, "cancelled"),
        (["stop", "stop", "save-partial"], True, False, True, "partial"),
        (["cancel"], False, True, False, "cancelled"),
        (["save-partial"], False, True, True, "partial"),
        (["invalid", "stop"], False, False, True, "captured"),
    ],
)
def test_terminal_choices(tmp_path, capsys, commands, incomplete, closed_early, saved, status):
    FakeSession.incomplete = incomplete
    FakeSession.closed_early = closed_early
    sequence = iter(commands)

    async def reader():
        return next(sequence)

    destination = tmp_path / "demo.recording.json"
    result = asyncio.run(
        record(
            "https://example.com/",
            ["example.com"],
            destination,
            tmp_path / "workflows",
            reader=reader,
            session_factory=FakeSession,
        )
    )
    assert result == (0 if saved else 1)
    assert destination.exists() == saved
    assert FakeSession.instances[-1].closed
    assert json.loads(capsys.readouterr().out)["status"] == status


def test_cancellation_closes_resources_without_saving(tmp_path):
    FakeSession.incomplete = False
    FakeSession.closed_early = False

    async def reader():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            record(
                "https://example.com/",
                ["example.com"],
                tmp_path / "a.recording.json",
                tmp_path / "workflows",
                reader=reader,
                session_factory=FakeSession,
            )
        )
    assert FakeSession.instances[-1].closed
    assert not list(tmp_path.iterdir())


def test_save_failure_closes_session_and_reports_safe_error(tmp_path, monkeypatch):
    FakeSession.incomplete = False
    FakeSession.closed_early = False

    async def reader():
        return "stop"

    def fail(*args):
        raise RecordingError("Could not save the draft.")

    monkeypatch.setattr("browser_to_mcp.recorder.command.save_draft", fail)
    with pytest.raises(RecordingError, match="Could not save"):
        asyncio.run(
            record(
                "https://example.com/",
                ["example.com"],
                tmp_path / "a.recording.json",
                tmp_path / "workflows",
                reader=reader,
                session_factory=FakeSession,
            )
        )
    assert FakeSession.instances[-1].closed


def test_ctrl_c_reports_discard(monkeypatch, tmp_path, capsys):
    def interrupted(coro):
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr("browser_to_mcp.recorder.command.asyncio.run", interrupted)
    assert (
        run_record(
            "https://example.com/",
            ["example.com"],
            tmp_path / "a.recording.json",
            tmp_path / "workflows",
        )
        == 130
    )
    assert json.loads(capsys.readouterr().out) == {"status": "cancelled", "saved": False}


def test_record_help_and_preflight(tmp_path, capsys):
    with pytest.raises(SystemExit) as caught:
        run(["record", "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    for expected in ["read-only", "non-sensitive", "--allow-domain", ".recording.json", "cancel"]:
        assert expected in help_text
    assert (
        run(
            [
                "record",
                "https://example.com",
                "--allow-domain",
                "other.com",
                "--draft",
                str(tmp_path / "demo.recording.json"),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().err)["category"] == "recording"


@pytest.mark.parametrize("value,expected", [("STOP\n", "stop"), ("", "cancel")])
def test_terminal_reader_handles_commands_and_eof(monkeypatch, value, expected):
    import io

    from browser_to_mcp.recorder.command import read_command

    monkeypatch.setattr("sys.stdin", io.StringIO(value))
    assert asyncio.run(read_command()) == expected


def test_terminal_reader_real_descriptor_keeps_blank_lines_distinct_from_eof(monkeypatch):
    import os

    from browser_to_mcp.recorder.command import read_command

    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"\nstop\n")
    os.close(write_fd)
    with os.fdopen(read_fd) as stream:
        monkeypatch.setattr("sys.stdin", stream)

        async def read_all():
            return [await read_command(), await read_command(), await read_command()]

        assert asyncio.run(read_all()) == ["", "stop", "cancel"]
