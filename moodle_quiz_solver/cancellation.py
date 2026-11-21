"""Cross-platform rootless cancellation seam with scoped armed() lifecycle."""

from __future__ import annotations

import importlib
import os
import select
import signal
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from types import FrameType
from typing import Any, Protocol, runtime_checkable

from moodle_quiz_solver.exceptions import CancellationError


@runtime_checkable
class Cancellation(Protocol):
    """Public cancellation seam for Auto Mode and AI adapter polling."""

    def armed(self) -> AbstractContextManager[None]:
        """Enter a scoped cancellation-monitoring context."""
        ...

    def is_cancelled(self) -> bool:
        """Return whether cancellation has been triggered and latched."""
        ...

    def raise_if_cancelled(self) -> None:
        """Raise CancellationError if cancellation has been triggered."""
        ...

    def reset(self) -> None:
        """Clear any latched cancellation state."""
        ...


class InMemoryCancellation:
    """Deterministic in-memory Cancellation adapter for tests."""

    def __init__(self) -> None:
        self._cancelled = False

    @contextmanager
    def armed(self) -> Iterator[None]:
        """Enter a no-op armed context without touching OS signals or terminal state."""
        yield

    def trigger(self) -> None:
        """Latch cancellation state."""
        self._cancelled = True

    def is_cancelled(self) -> bool:
        """Return whether cancellation has been triggered and latched."""
        return self._cancelled

    def raise_if_cancelled(self) -> None:
        """Raise CancellationError if cancellation is latched."""
        if self.is_cancelled():
            raise CancellationError("Operation cancelled by user.")

    def reset(self) -> None:
        """Clear latched cancellation state."""
        self._cancelled = False


class TerminalEscMonitor(Protocol):
    """Internal seam for platform-specific non-blocking ESC key detection."""

    def enter(self) -> None:
        """Configure terminal for non-blocking ESC polling."""
        ...

    def exit(self) -> None:
        """Restore terminal state configured by enter()."""
        ...

    def poll_escape(self) -> bool:
        """Return True if an ESC keypress is detected non-blockingly."""
        ...


class _StdinStream(Protocol):
    def isatty(self) -> bool: ...
    def fileno(self) -> int: ...


class _TermiosModule(Protocol):
    TCSADRAIN: int

    def tcgetattr(self, fd: int) -> Any: ...
    def tcsetattr(self, fd: int, when: int, attributes: Any) -> None: ...


class _TtyModule(Protocol):
    def setcbreak(self, fd: int) -> None: ...


_SelectFn = Callable[
    [list[int], list[int], list[int], float],
    tuple[list[int], list[int], list[int]],
]
_ReadFn = Callable[[int, int], bytes]


def _import_optional_module(name: str) -> Any | None:
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


class PosixEscMonitor:
    """Rootless non-blocking ESC monitor using termios/tty/select on POSIX TTYs."""

    def __init__(
        self,
        *,
        stdin: _StdinStream | None = None,
        termios_mod: _TermiosModule | None = None,
        tty_mod: _TtyModule | None = None,
        select_fn: _SelectFn | None = None,
        read_fn: _ReadFn | None = None,
    ) -> None:
        self._stdin: _StdinStream | None = stdin if stdin is not None else sys.stdin
        self._termios: _TermiosModule | None = (
            termios_mod if termios_mod is not None else _import_optional_module("termios")
        )
        self._tty: _TtyModule | None = (
            tty_mod if tty_mod is not None else _import_optional_module("tty")
        )
        self._select: _SelectFn = select_fn if select_fn is not None else select.select
        self._read: _ReadFn = read_fn if read_fn is not None else os.read
        self._fd: int | None = None
        self._saved_attrs: Any = None

    def enter(self) -> None:
        """Put TTY stdin into cbreak mode, saving original attributes."""
        if self._stdin is None or self._termios is None or self._tty is None:
            return
        try:
            if not self._stdin.isatty():
                return
            fd = self._stdin.fileno()
            saved_attrs = self._termios.tcgetattr(fd)
            self._tty.setcbreak(fd)
        except (OSError, ValueError):
            self._fd = None
            self._saved_attrs = None
            return
        self._fd = fd
        self._saved_attrs = saved_attrs

    def exit(self) -> None:
        """Restore saved terminal attributes if cbreak mode was entered."""
        fd = self._fd
        saved_attrs = self._saved_attrs
        self._fd = None
        self._saved_attrs = None
        if fd is None or saved_attrs is None or self._termios is None:
            return
        try:
            self._termios.tcsetattr(fd, self._termios.TCSADRAIN, saved_attrs)
        except OSError:
            pass

    def _has_pending_byte(self, fd: int) -> bool:
        try:
            readable, _, _ = self._select([fd], [], [], 0)
        except (OSError, ValueError):
            return False
        return fd in readable

    def poll_escape(self) -> bool:
        """Non-blockingly drain pending bytes and return True on standalone ESC."""
        if self._fd is None:
            return False
        fd = self._fd
        while self._has_pending_byte(fd):
            data = self._read(fd, 1)
            if not data:
                return False
            if data == b"\x1b":
                if not self._has_pending_byte(fd):
                    return True
                next_byte = self._read(fd, 1)
                if next_byte not in (b"[", b"O"):
                    return True
                # Drain CSI/SS3 escape sequence (e.g. arrow/function keys) without cancelling.
                while self._has_pending_byte(fd):
                    if not self._read(fd, 1):
                        break
        return False


class _MsvcrtModule(Protocol):
    def kbhit(self) -> bool: ...
    def getwch(self) -> str: ...


class WindowsEscMonitor:
    """Rootless non-blocking ESC monitor using standard-library msvcrt on Windows."""

    def __init__(
        self,
        *,
        stdin: _StdinStream | None = None,
        msvcrt_mod: _MsvcrtModule | None = None,
    ) -> None:
        self._stdin: _StdinStream | None = stdin if stdin is not None else sys.stdin
        self._msvcrt: _MsvcrtModule | None = (
            msvcrt_mod if msvcrt_mod is not None else _import_optional_module("msvcrt")
        )
        self._active = False

    def enter(self) -> None:
        """Enable console ESC polling if stdin is an interactive TTY."""
        if self._stdin is None or self._msvcrt is None:
            return
        try:
            self._active = bool(self._stdin.isatty())
        except (OSError, ValueError):
            self._active = False

    def exit(self) -> None:
        """Disable console ESC polling."""
        self._active = False

    def poll_escape(self) -> bool:
        """Non-blockingly drain console keystrokes and return True on ESC."""
        if not self._active or self._msvcrt is None:
            return False
        while self._msvcrt.kbhit():
            ch = self._msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                # Consume second code of extended key (arrows, function keys).
                if self._msvcrt.kbhit():
                    self._msvcrt.getwch()
                continue
            if ch == "\x1b":
                return True
        return False


def _create_default_monitor() -> TerminalEscMonitor:
    if sys.platform == "win32":
        return WindowsEscMonitor()
    return PosixEscMonitor()


class TerminalCancellation:
    """Rootless cross-platform cancellation using scoped ESC polling and SIGINT."""

    def __init__(self, monitor: TerminalEscMonitor | None = None) -> None:
        self._monitor: TerminalEscMonitor = (
            monitor if monitor is not None else _create_default_monitor()
        )
        self._cancelled = False
        self._armed_depth = 0

    def _handle_sigint(self, _signum: int, _frame: FrameType | None) -> None:
        self._cancelled = True

    @contextmanager
    def armed(self) -> Iterator[None]:
        """Arm SIGINT and terminal ESC detection for the duration of the context."""
        if self._armed_depth > 0:
            self._armed_depth += 1
            try:
                yield
            finally:
                self._armed_depth -= 1
            return

        is_main_thread = threading.current_thread() is threading.main_thread()
        prev_handler: Any = None
        if is_main_thread:
            prev_handler = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, self._handle_sigint)

        self._armed_depth = 1
        try:
            self._monitor.enter()
            try:
                yield
            finally:
                self._monitor.exit()
        finally:
            self._armed_depth = 0
            if is_main_thread and prev_handler is not None:
                signal.signal(signal.SIGINT, prev_handler)

    def is_cancelled(self) -> bool:
        """Return True if cancellation was latched via SIGINT or ESC."""
        if self._cancelled:
            return True
        if self._armed_depth > 0 and self._monitor.poll_escape():
            self._cancelled = True
        return self._cancelled

    def raise_if_cancelled(self) -> None:
        """Raise CancellationError if cancellation is latched."""
        if self.is_cancelled():
            raise CancellationError("Operation cancelled by user.")

    def reset(self) -> None:
        """Clear latched cancellation state."""
        self._cancelled = False

