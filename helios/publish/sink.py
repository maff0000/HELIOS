"""Publication sinks — where a published envelope actually goes.

HELIOS publishes normalised strategy state to FALCON. *How* it leaves the
process is an operational decision, not a strategy one, so the engine writes
canonical JSON lines to a sink and the sink is chosen by external
configuration (:mod:`helios.publish.config`).

Every sink receives the **already-serialised canonical payload**, never the
envelope. One place decides the bytes, so every sink emits identical bytes for
identical state and a golden payload captured from one sink is byte-identical
to the same state published through another.

The wire format is JSON Lines: one canonical JSON object per line, terminated
by a single ``\\n``. It is append-only, streamable, and trivially tailable.

What is deliberately absent
---------------------------
There is **no network sink, and no state service**. The PID requires "no
public internal state service by default", so the sinks here write to a file
or to an already-open stream and nothing else. Nothing in this package opens a
socket, binds a port, or speaks a wire protocol; ``tests/test_execution_blind``
enforces that no module under ``helios/`` may even import a networking library.
A consumer reads HELIOS output the way it reads any other file or stream.
"""

from __future__ import annotations

from pathlib import Path
from typing import IO, Optional, Protocol, runtime_checkable

from helios.errors import ConfigurationError, HeliosError

#: Terminator written after each canonical payload. JSON Lines, always LF.
RECORD_TERMINATOR = "\n"


@runtime_checkable
class PublicationSink(Protocol):
    """Where canonical published payloads are written.

    Implementations must be synchronous and must not reorder, batch-merge or
    rewrite payloads: what HELIOS hands over is what a consumer reads.
    """

    @property
    def description(self) -> str:
        """A short, log-safe description of this destination."""
        ...

    def emit(self, payload: str) -> None:
        """Write one canonical payload."""
        ...

    def flush(self) -> None:
        """Make everything written so far visible to a consumer."""
        ...

    def close(self) -> None:
        """Release the destination. Emitting afterwards must fail loudly."""
        ...


class _ClosableSink:
    """Shared close/flush bookkeeping for the concrete sinks."""

    __slots__ = ("_closed",)

    def __init__(self) -> None:
        self._closed = False

    @property
    def is_closed(self) -> bool:
        return self._closed

    def _refuse_if_closed(self) -> None:
        if self._closed:
            raise HeliosError(
                "publication sink is closed; a payload published after close "
                "would be silently lost",
                sink=self.description,  # type: ignore[attr-defined]
            )

    def __enter__(self):  # noqa: ANN204 - context manager protocol
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class InMemorySink(_ClosableSink):
    """Collects payloads in memory. For tests and dry runs.

    Publishes nowhere durable: when the process ends the payloads are gone.
    It exists so that the publication path can be exercised end to end — the
    same serialisation, the same ordering, the same byte output — without a
    filesystem.
    """

    __slots__ = ("_records",)

    def __init__(self) -> None:
        super().__init__()
        self._records: list[str] = []

    @property
    def description(self) -> str:
        return "memory"

    @property
    def records(self) -> tuple[str, ...]:
        """Every payload emitted, in publication order."""
        return tuple(self._records)

    def emit(self, payload: str) -> None:
        self._refuse_if_closed()
        self._records.append(payload)

    def flush(self) -> None:
        self._refuse_if_closed()

    def close(self) -> None:
        self._closed = True


class StreamSink(_ClosableSink):
    """Writes JSON lines to an already-open text stream.

    The stream is supplied by the caller (``sys.stdout`` for a container that
    logs its output, or any writable text file object). By default the sink
    does not close a stream it did not open.
    """

    __slots__ = ("_stream", "_label", "_owns_stream")

    def __init__(self, stream: IO[str], *, label: str, owns_stream: bool = False) -> None:
        super().__init__()
        if not hasattr(stream, "write"):
            raise ConfigurationError(
                "publication stream must be a writable text stream",
                received_type=type(stream).__name__,
            )
        self._stream = stream
        self._label = label
        self._owns_stream = owns_stream

    @property
    def description(self) -> str:
        return f"stream:{self._label}"

    def emit(self, payload: str) -> None:
        self._refuse_if_closed()
        self._stream.write(payload + RECORD_TERMINATOR)

    def flush(self) -> None:
        self._refuse_if_closed()
        if hasattr(self._stream, "flush"):
            self._stream.flush()

    def close(self) -> None:
        if self._closed:
            return
        try:
            if hasattr(self._stream, "flush"):
                self._stream.flush()
        finally:
            self._closed = True
            if self._owns_stream:
                self._stream.close()


class FileSink(_ClosableSink):
    """Appends JSON lines to a file.

    The parent directory must already exist. HELIOS does not create a
    destination it was not configured for: a mistyped path must fail loudly at
    startup rather than quietly produce an unread file somewhere unexpected.

    The file is opened lazily on the first publication and every record is
    flushed, so a consumer tailing the file sees each state as it is published
    rather than when a buffer happens to fill.
    """

    __slots__ = ("_path", "_handle", "_encoding")

    def __init__(self, path: Path | str, *, encoding: str = "utf-8") -> None:
        super().__init__()
        resolved = Path(path)
        parent = resolved.parent if str(resolved.parent) else Path(".")
        if not parent.is_dir():
            raise ConfigurationError(
                "publication directory does not exist",
                directory=str(parent),
                path=str(resolved),
            )
        if resolved.exists() and not resolved.is_file():
            raise ConfigurationError(
                "publication path exists and is not a file", path=str(resolved)
            )
        self._path = resolved
        self._encoding = encoding
        self._handle: Optional[IO[str]] = None

    @property
    def description(self) -> str:
        return f"file:{self._path}"

    @property
    def path(self) -> Path:
        return self._path

    def _open(self) -> IO[str]:
        if self._handle is None:
            try:
                self._handle = self._path.open(
                    "a", encoding=self._encoding, newline=RECORD_TERMINATOR
                )
            except OSError as exc:
                raise ConfigurationError(
                    "publication file could not be opened for appending",
                    path=str(self._path),
                    detail=str(exc),
                ) from exc
        return self._handle

    def emit(self, payload: str) -> None:
        self._refuse_if_closed()
        handle = self._open()
        handle.write(payload + RECORD_TERMINATOR)
        handle.flush()

    def flush(self) -> None:
        self._refuse_if_closed()
        if self._handle is not None:
            self._handle.flush()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._handle is not None:
            self._handle.close()
            self._handle = None
