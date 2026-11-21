from __future__ import annotations

import io
import signal

import pytest

from moodle_quiz_solver.cancellation import (
    Cancellation,
    InMemoryCancellation,
    PosixEscMonitor,
    TerminalCancellation,
    WindowsEscMonitor,
)
from moodle_quiz_solver.exceptions import CancellationError


def test_in_memory_cancellation_latches_raises_and_resets() -> None:
    cancellation: Cancellation = InMemoryCancellation()

    assert cancellation.is_cancelled() is False
    cancellation.raise_if_cancelled()

    with cancellation.armed():
        assert isinstance(cancellation, InMemoryCancellation)
        cancellation.trigger()
        assert cancellation.is_cancelled() is True
        with pytest.raises(CancellationError):
            cancellation.raise_if_cancelled()

    # Remains latched after exiting armed() until reset() is called
    assert cancellation.is_cancelled() is True
    with pytest.raises(CancellationError):
        cancellation.raise_if_cancelled()

    cancellation.reset()
    assert cancellation.is_cancelled() is False
    cancellation.raise_if_cancelled()


def test_terminal_cancellation_arms_sigint_latches_and_restores_on_exit() -> None:
    original_handler = signal.getsignal(signal.SIGINT)
    cancellation = TerminalCancellation()

    with cancellation.armed():
        armed_handler = signal.getsignal(signal.SIGINT)
        assert armed_handler is not original_handler
        assert cancellation.is_cancelled() is False

        signal.raise_signal(signal.SIGINT)
        assert cancellation.is_cancelled() is True
        with pytest.raises(CancellationError):
            cancellation.raise_if_cancelled()

    assert signal.getsignal(signal.SIGINT) is original_handler

    assert cancellation.is_cancelled() is True
    cancellation.reset()
    assert cancellation.is_cancelled() is False


def test_terminal_cancellation_restores_sigint_handler_on_exception() -> None:
    original_handler = signal.getsignal(signal.SIGINT)
    cancellation = TerminalCancellation()

    with pytest.raises(RuntimeError, match="boom"):
        with cancellation.armed():
            assert signal.getsignal(signal.SIGINT) is not original_handler
            raise RuntimeError("boom")

    assert signal.getsignal(signal.SIGINT) is original_handler


class _FakeStdin:
    def __init__(self, *, tty: bool, fd: int = 7) -> None:
        self._tty = tty
        self._fd = fd

    def isatty(self) -> bool:
        return self._tty

    def fileno(self) -> int:
        return self._fd


class _FakeTermios:
    TCSADRAIN = 1

    def __init__(self, attrs: object = None, *, fail_if_called: bool = False) -> None:
        self.attrs = attrs if attrs is not None else ["orig"]
        self.fail_if_called = fail_if_called
        self.getattr_calls: list[int] = []
        self.setattr_calls: list[tuple[int, int, object]] = []

    def tcgetattr(self, fd: int) -> object:
        if self.fail_if_called:
            raise AssertionError("tcgetattr must not be called for non-TTY stdin")
        self.getattr_calls.append(fd)
        return self.attrs

    def tcsetattr(self, fd: int, when: int, attributes: object) -> None:
        if self.fail_if_called:
            raise AssertionError("tcsetattr must not be called for non-TTY stdin")
        self.setattr_calls.append((fd, when, attributes))


class _FakeTty:
    def __init__(self, *, fail_if_called: bool = False) -> None:
        self.fail_if_called = fail_if_called
        self.setcbreak_calls: list[int] = []

    def setcbreak(self, fd: int) -> None:
        if self.fail_if_called:
            raise AssertionError("setcbreak must not be called for non-TTY stdin")
        self.setcbreak_calls.append(fd)


def test_posix_monitor_degrades_safely_to_sigint_when_not_tty() -> None:
    monitor = PosixEscMonitor(
        stdin=_FakeStdin(tty=False),
        termios_mod=_FakeTermios(fail_if_called=True),
        tty_mod=_FakeTty(fail_if_called=True),
    )
    cancellation = TerminalCancellation(monitor=monitor)

    with cancellation.armed():
        assert cancellation.is_cancelled() is False
        signal.raise_signal(signal.SIGINT)
        assert cancellation.is_cancelled() is True


def test_posix_monitor_restores_terminal_attributes_on_normal_and_exception_exit() -> None:
    termios_mod = _FakeTermios(attrs=["saved-state"])
    tty_mod = _FakeTty()
    monitor = PosixEscMonitor(
        stdin=_FakeStdin(tty=True, fd=42),
        termios_mod=termios_mod,
        tty_mod=tty_mod,
        select_fn=lambda _r, _w, _x, _t: ([], [], []),
        read_fn=lambda _fd, _n: b"",
    )
    cancellation = TerminalCancellation(monitor=monitor)

    with cancellation.armed():
        assert termios_mod.getattr_calls == [42]
        assert tty_mod.setcbreak_calls == [42]
        assert termios_mod.setattr_calls == []

    assert termios_mod.setattr_calls == [(42, termios_mod.TCSADRAIN, ["saved-state"])]

    with pytest.raises(RuntimeError, match="boom"):
        with cancellation.armed():
            raise RuntimeError("boom")

    assert termios_mod.setattr_calls == [
        (42, termios_mod.TCSADRAIN, ["saved-state"]),
        (42, termios_mod.TCSADRAIN, ["saved-state"]),
    ]


def test_posix_monitor_detects_esc_and_ignores_other_keys_and_escape_sequences() -> None:
    buffer = bytearray(b"a\x1b[A")

    def fake_select(
        rlist: list[int], _w: list[int], _x: list[int], _timeout: float
    ) -> tuple[list[int], list[int], list[int]]:
        return (rlist if buffer else [], [], [])

    def fake_read(_fd: int, n: int) -> bytes:
        chunk = bytes(buffer[:n])
        del buffer[:n]
        return chunk

    monitor = PosixEscMonitor(
        stdin=_FakeStdin(tty=True, fd=9),
        termios_mod=_FakeTermios(),
        tty_mod=_FakeTty(),
        select_fn=fake_select,
        read_fn=fake_read,
    )
    cancellation = TerminalCancellation(monitor=monitor)

    with cancellation.armed():
        # 'a' and arrow sequence '\x1b[A' should be drained without cancelling
        assert cancellation.is_cancelled() is False
        assert bytes(buffer) == b""

        # Standalone ESC should latch cancellation
        buffer.extend(b"\x1b")
        assert cancellation.is_cancelled() is True
        with pytest.raises(CancellationError):
            cancellation.raise_if_cancelled()

        # ESC followed by a regular key (not '[' or 'O') also latches cancellation
        cancellation.reset()
        buffer.extend(b"\x1ba")
        assert cancellation.is_cancelled() is True


class _FakeMsvcrt:
    def __init__(self, keys: list[str] | None = None, *, fail_if_called: bool = False) -> None:
        self.keys = keys if keys is not None else []
        self.fail_if_called = fail_if_called

    def kbhit(self) -> bool:
        if self.fail_if_called:
            raise AssertionError("kbhit must not be called for non-TTY stdin")
        return bool(self.keys)

    def getwch(self) -> str:
        if self.fail_if_called:
            raise AssertionError("getwch must not be called for non-TTY stdin")
        return self.keys.pop(0)


def test_windows_monitor_detects_esc_and_ignores_other_and_extended_keys() -> None:
    msvcrt_mod = _FakeMsvcrt(["a", "\xe0", "H"])
    monitor = WindowsEscMonitor(stdin=_FakeStdin(tty=True), msvcrt_mod=msvcrt_mod)
    cancellation = TerminalCancellation(monitor=monitor)

    with cancellation.armed():
        assert cancellation.is_cancelled() is False
        assert msvcrt_mod.keys == []

        msvcrt_mod.keys.append("\x1b")
        assert cancellation.is_cancelled() is True
        with pytest.raises(CancellationError):
            cancellation.raise_if_cancelled()


def test_windows_monitor_degrades_safely_to_sigint_when_not_tty() -> None:
    monitor = WindowsEscMonitor(
        stdin=_FakeStdin(tty=False),
        msvcrt_mod=_FakeMsvcrt(fail_if_called=True),
    )
    cancellation = TerminalCancellation(monitor=monitor)

    with cancellation.armed():
        assert cancellation.is_cancelled() is False
        signal.raise_signal(signal.SIGINT)
        assert cancellation.is_cancelled() is True


def test_interactive_input_works_after_armed_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    cancellation = TerminalCancellation()

    with cancellation.armed():
        pass

    monkeypatch.setattr("sys.stdin", io.StringIO("user answer\n"))
    assert input("Prompt: ") == "user answer"



