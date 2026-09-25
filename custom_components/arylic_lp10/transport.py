"""Persistent, serialized LP10 TCP transport (standard library only).

One owner per player per process is required. This does not coordinate separate
HA/add-on processes. Replies have no request IDs: a same-key unsolicited reply
arriving during a query cannot be distinguished from the requested response.
"""

from __future__ import annotations

from contextlib import contextmanager
import socket
import threading
import time


class TransportError(OSError):
    """A transaction failed; writes must never be automatically replayed."""


class PersistentConnection:
    """Lazy connection, single reader, atomic batches and demand-only recovery."""

    MAX_BUFFER = 8192

    def __init__(self, host: str, port: int = 2018, *,
                 connect_timeout: float = 0.8, backoff_initial: float = 1.0,
                 backoff_max: float = 30.0) -> None:
        self.host, self.port = host, port
        self.connect_timeout = connect_timeout
        self.backoff_initial, self.backoff_max = backoff_initial, backoff_max
        self._transaction_lock = threading.RLock()
        self._condition = threading.Condition()
        self._socket: socket.socket | None = None
        self._reader: threading.Thread | None = None
        self._closed = False
        self._failures = 0
        self._retry_at = 0.0
        self._connected_at = 0.0
        self._expected: str | None = None
        self._reply: str | None = None
        self._error: TransportError | None = None
        self._counters = {"connect_attempts": 0, "connections_opened": 0,
                          "disconnects": 0, "failures": 0, "queries": 0, "writes": 0,
                          "matched_replies": 0, "unsolicited_frames": 0}

    @property
    def diagnostics(self) -> dict:
        """Local counters only: reading these never contacts the player."""
        with self._condition:
            return {**self._counters, "connected": self._socket is not None,
                    "closed": self._closed,
                    "retry_in_seconds": max(0.0, self._retry_at - time.monotonic())}

    @contextmanager
    def transaction(self, timeout: float = 10.0):
        """Prevent commands from interleaving across a logical read/write batch."""
        if not self._transaction_lock.acquire(timeout=timeout):
            raise TransportError("LP10 control transaction is busy")
        try:
            yield self
        finally:
            self._transaction_lock.release()

    def _connect(self) -> socket.socket:
        with self._condition:
            if self._closed:
                raise TransportError("LP10 connection is closed")
            if self._socket is not None:
                return self._socket
            remaining = self._retry_at - time.monotonic()
            if remaining > 0:
                raise TransportError(f"LP10 reconnect deferred for {remaining:.1f}s")
            try:
                self._counters["connect_attempts"] += 1
                sock = socket.create_connection((self.host, self.port), self.connect_timeout)
                # A single fixed timeout avoids racing recv/send timeout changes.
                sock.settimeout(0.2)
            except OSError as exc:
                self._record_failure()
                raise TransportError("Could not connect to LP10 control port") from exc
            self._socket = sock
            self._connected_at = time.monotonic()
            self._counters["connections_opened"] += 1
            self._error = None
            self._reader = threading.Thread(target=self._receive, args=(sock,),
                                            name=f"lp10-reader-{self.host}", daemon=True)
            self._reader.start()
            return sock

    def _record_failure(self) -> None:
        self._counters["failures"] += 1
        self._failures = min(self._failures + 1, 16)
        delay = min(self.backoff_max, self.backoff_initial * 2 ** (self._failures - 1))
        self._retry_at = time.monotonic() + delay

    def _fail(self, sock: socket.socket, message: str) -> None:
        with self._condition:
            if self._socket is not sock:
                return
            self._socket = None
            self._counters["disconnects"] += 1
            self._error = TransportError(message)
            self._record_failure()
            self._condition.notify_all()
        self._close_socket(sock)

    @staticmethod
    def _close_socket(sock: socket.socket) -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()

    def _receive(self, sock: socket.socket) -> None:
        pending = bytearray()
        try:
            while True:
                with self._condition:
                    if self._socket is not sock:
                        return
                try:
                    chunk = sock.recv(1024)
                except socket.timeout:
                    continue
                if not chunk:
                    self._fail(sock, "LP10 closed the control connection")
                    return
                pending.extend(chunk)
                while b";" in pending:
                    frame, _, remainder = pending.partition(b";")
                    pending = bytearray(remainder)
                    if len(frame) > self.MAX_BUFFER:
                        raise TransportError("LP10 control frame exceeded buffer limit")
                    reply = frame.decode("ascii", errors="replace").strip("\x00\r\n ") + ";"
                    with self._condition:
                        if self._socket is not sock:
                            return
                        if self._expected and self._reply is None and reply.startswith(self._expected):
                            self._reply = reply
                            self._counters["matched_replies"] += 1
                            self._condition.notify_all()
                        else:
                            self._counters["unsolicited_frames"] += 1
                    # Unsolicited/old complete frames are discarded, even idle.
                if len(pending) > self.MAX_BUFFER:
                    raise TransportError("LP10 unterminated frame exceeded buffer limit")
        except OSError as exc:
            self._fail(sock, f"LP10 receive failed: {exc}")

    @staticmethod
    def _encode(command: str) -> bytes:
        if not command.endswith(";") or command.count(";") != 1 or len(command) > 1024:
            raise ValueError("Expected one bounded semicolon-terminated LP10 command")
        return command.encode("ascii")

    def query(self, command: str, timeout: float = 0.35,
              expected_prefix: str | None = None) -> str:
        payload = self._encode(command)
        with self.transaction():
            sock = self._connect()
            deadline = time.monotonic() + timeout
            with self._condition:
                self._expected = expected_prefix or command.rstrip(";") + ":"
                self._reply = None
                try:
                    # Reader publishes responses under this same condition.
                    sock.sendall(payload)
                    self._counters["queries"] += 1
                    while self._reply is None and self._socket is sock and not self._closed:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        self._condition.wait(remaining)
                    if self._reply is not None:
                        # A device can answer early fields but consistently fail
                        # later ones. Only a stable session resets its backoff.
                        if time.monotonic() - self._connected_at >= 60.0:
                            self._failures = 0
                            self._retry_at = 0.0
                        return self._reply
                    error = self._error or TransportError("LP10 query timed out")
                except OSError as exc:
                    error = TransportError(f"LP10 query send failed: {exc}")
                finally:
                    self._expected = None
                    self._reply = None
            # Retire timed-out streams: a late same-key reply must not satisfy
            # the next query. Reconnect only on a later demand after backoff.
            self._fail(sock, str(error))
            raise error

    def write(self, command: str, timeout: float = 0.18) -> None:
        """Send once; optional acknowledgements are drained, never required.

        timeout is accepted for caller compatibility; there is no ACK wait.
        Successful send is not proof the player applied the write.
        """
        payload = self._encode(command)
        with self.transaction():
            sock = self._connect()
            try:
                sock.sendall(payload)
                with self._condition:
                    self._counters["writes"] += 1
            except OSError as exc:
                self._fail(sock, "LP10 write failed; application is unknown")
                raise TransportError("LP10 write failed; application is unknown") from exc

    def close(self) -> None:
        """Terminal shutdown, including an in-flight query; never reconnect."""
        with self._condition:
            self._closed = True
            sock, self._socket = self._socket, None
            if sock is not None:
                self._counters["disconnects"] += 1
            self._error = TransportError("LP10 connection is closed")
            reader = self._reader
            self._condition.notify_all()
        if sock is not None:
            self._close_socket(sock)
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=0.5)
