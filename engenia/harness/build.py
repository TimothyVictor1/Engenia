"""Running a stage build under the harness: captured output, and transcripts that survive it."""

from __future__ import annotations

import shutil
import tempfile
import threading
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout, suppress
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

MAX_OUTPUT_CHARS = 8000

# redirect_stdout swaps a process-wide global. Two captures running in different threads would
# restore each other's streams in the wrong order, so captures take turns. Stages run in separate
# processes are unaffected.
_CAPTURE_LOCK = threading.RLock()


@dataclass(frozen=True)
class BuildResult:
    stdout: str
    stderr: str
    exit_code: int


def _tail(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return f"[truncated {len(text) - MAX_OUTPUT_CHARS} chars]\n" + text[-MAX_OUTPUT_CHARS:]


def run_captured(fn: Callable[[], None]) -> BuildResult:
    """Call fn, capturing Python-level stdout and stderr. An exception becomes exit code 1.

    `SystemExit(n)` becomes exit code n. Output of child processes started by fn is not captured.
    """
    out, err = StringIO(), StringIO()
    exit_code = 0
    with _CAPTURE_LOCK, redirect_stdout(out), redirect_stderr(err):
        try:
            fn()
        except SystemExit as exc:
            if exc.code is None:
                exit_code = 0
            elif isinstance(exc.code, int):
                exit_code = exc.code
            else:
                err.write(f"{exc.code}\n")
                exit_code = 1
        except Exception:
            err.write(traceback.format_exc())
            exit_code = 1
    return BuildResult(
        stdout=_tail(out.getvalue()), stderr=_tail(err.getvalue()), exit_code=exit_code
    )


@contextmanager
def preserved_dir(directory: Path) -> Iterator[None]:
    """Keep `directory` and its contents across a block that may delete its parent.

    A stage's build() replaces build/<stage>/, which is where transcripts live. The directory is
    moved aside for the duration and put back afterwards, over whatever the block created.
    """
    if not directory.is_dir():
        yield
        return
    holder = Path(tempfile.mkdtemp(prefix=".keep-", dir=directory.parent.parent))
    parked = holder / directory.name
    shutil.move(str(directory), str(parked))
    with suppress(OSError):
        directory.parent.rmdir()  # leave no empty build dir behind for a build that expects none
    try:
        yield
    finally:
        directory.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(parked, directory, dirs_exist_ok=True)
        shutil.rmtree(holder, ignore_errors=True)
