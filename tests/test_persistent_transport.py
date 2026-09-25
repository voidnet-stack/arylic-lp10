"""Offline socket-fake regression tests; never contact a player."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import queue
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch

COMPONENT = Path(__file__).parents[1] / "custom_components" / "arylic_lp10"


def load(name):
    spec = importlib.util.spec_from_file_location(name, COMPONENT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


transport = load("transport")
protocol = load("protocol")


class FakeSocket:
    def __init__(self, responder=None):
        self.incoming = queue.Queue()
        self.sent = []
        self.closed = False
        self.responder = responder

    def settimeout(self, value):
        pass

    def sendall(self, payload):
        if self.closed:
            raise OSError("closed")
        self.sent.append(payload.decode())
        if self.responder:
            self.responder(self, payload.decode())

    def recv(self, size):
        try:
            return self.incoming.get(timeout=0.02)
        except queue.Empty:
            raise socket.timeout()

    def shutdown(self, how):
        self.closed = True
        self.incoming.put(b"")

    def close(self):
        self.closed = True


def status_response(sock, command):
    key = command.rstrip(";")
    if key == "SRC":
        value = "NET"
    elif key == "VER":
        value = "test"
    elif key == "MXV":
        value = "100"
    else:
        value = "0"
    sock.incoming.put(f"{key}:{value};".encode())


class PersistentTransportTests(unittest.TestCase):
    def make_connection(self, sock, **kwargs):
        factory = patch.object(transport.socket, "create_connection", return_value=sock)
        mocked = factory.start()
        self.addCleanup(factory.stop)
        connection = transport.PersistentConnection("10.0.0.1", **kwargs)
        self.addCleanup(connection.close)
        return connection, mocked

    def test_fragmented_and_coalesced_frames_match_only_requested_key(self):
        def respond(sock, _):
            sock.incoming.put(b"VOL:80;SRC:N")
            sock.incoming.put(b"ET;LED:1;")
        connection, factory = self.make_connection(FakeSocket(respond))
        self.assertEqual(connection.query("SRC;"), "SRC:NET;")
        self.assertEqual(connection.query("SRC;"), "SRC:NET;")
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(connection.diagnostics["matched_replies"], 2)

    def test_status_reads_and_write_share_one_socket(self):
        sock = FakeSocket(status_response)
        _, factory = self.make_connection(sock)
        client = protocol.LP10Client("10.0.0.1")
        self.addCleanup(client.close)
        self.assertEqual(client.query_status()["source"], "NET")
        client.set_volume(5)
        self.assertEqual(client.query_status()["source"], "NET")
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(len(sock.sent), 27)
        self.assertFalse(sock.closed)

    def test_idle_unsolicited_frames_are_drained_without_reconnect(self):
        sock = FakeSocket(status_response)
        connection, factory = self.make_connection(sock)
        connection.query("SRC;")
        sock.incoming.put(b"VOL:55;" * 2000)
        deadline = time.monotonic() + 1
        while connection.diagnostics["unsolicited_frames"] < 2000 and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(connection.diagnostics["unsolicited_frames"], 2000)
        self.assertEqual(connection.query("SRC;"), "SRC:NET;")
        self.assertEqual(factory.call_count, 1)

    def test_query_timeout_does_not_return_unrelated_or_partial_reply(self):
        sock = FakeSocket(lambda s, _: s.incoming.put(b"VOL:50;SRC:NE"))
        connection, factory = self.make_connection(sock)
        with self.assertRaisesRegex(transport.TransportError, "timed out"):
            connection.query("SRC;", timeout=0.03)
        self.assertTrue(sock.closed)
        with self.assertRaisesRegex(transport.TransportError, "deferred"):
            connection.query("SRC;")
        self.assertEqual(factory.call_count, 1)

    def test_eof_reconnects_only_on_demand_after_backoff(self):
        first = FakeSocket(lambda s, _: s.incoming.put(b""))
        connection, factory = self.make_connection(first, backoff_initial=0.03)
        with self.assertRaises(transport.TransportError):
            connection.query("SRC;")
        with self.assertRaisesRegex(transport.TransportError, "deferred"):
            connection.query("SRC;")
        time.sleep(0.05)
        self.assertEqual(factory.call_count, 1)
        factory.return_value = FakeSocket(status_response)
        self.assertEqual(connection.query("SRC;"), "SRC:NET;")
        self.assertEqual(factory.call_count, 2)

    def test_failed_write_is_not_replayed(self):
        def fail(sock, _):
            raise OSError("partly sent")
        sock = FakeSocket(fail)
        connection, factory = self.make_connection(sock)
        with self.assertRaisesRegex(transport.TransportError, "unknown"):
            connection.write("VOL:5;")
        self.assertEqual(sock.sent, ["VOL:5;"])
        self.assertEqual(factory.call_count, 1)

    def test_write_without_ack_is_successful_send_only(self):
        sock = FakeSocket()
        connection, _ = self.make_connection(sock)
        self.assertIsNone(connection.write("VOL:5;"))
        self.assertFalse(sock.closed)
        self.assertEqual(connection.diagnostics["writes"], 1)

    def test_oversize_unterminated_frame_fails_boundedly(self):
        sock = FakeSocket(lambda s, _: s.incoming.put(b"x" * 9000))
        connection, _ = self.make_connection(sock)
        with self.assertRaisesRegex(transport.TransportError, "buffer limit"):
            connection.query("SRC;")

    def test_atomic_batches_cannot_interleave(self):
        sock = FakeSocket(status_response)
        connection, _ = self.make_connection(sock)
        def batch(first, second):
            with connection.transaction():
                connection.query(first)
                time.sleep(0.01)
                connection.query(second)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(batch, "SRC;", "VOL;")
            b = pool.submit(batch, "LED;", "VER;")
            a.result()
            b.result()
        self.assertIn(sock.sent, [["SRC;", "VOL;", "LED;", "VER;"],
                                 ["LED;", "VER;", "SRC;", "VOL;"]])

    def test_close_interrupts_waiter_and_is_terminal(self):
        sent = threading.Event()
        sock = FakeSocket(lambda s, _: sent.set())
        connection, factory = self.make_connection(sock)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(connection.query, "SRC;", 5)
            self.assertTrue(sent.wait(1))
            connection.close()
            with self.assertRaises(transport.TransportError):
                pending.result(timeout=1)
        with self.assertRaisesRegex(transport.TransportError, "closed"):
            connection.write("VOL:5;")
        self.assertEqual(factory.call_count, 1)
        self.assertFalse(connection._reader.is_alive())

    def test_failed_connects_back_off_exponentially(self):
        connection, factory = self.make_connection(FakeSocket())
        factory.side_effect = OSError("unreachable")
        for expected in (1, 2, 4, 8, 16, 30, 30):
            connection._retry_at = 0
            with self.assertRaises(transport.TransportError):
                connection.query("SRC;")
            self.assertAlmostEqual(connection.diagnostics["retry_in_seconds"], expected, delta=.1)

    def test_partial_progress_does_not_reset_reconnect_backoff(self):
        def respond(sock, command):
            if command == "SRC;":
                sock.incoming.put(b"SRC:NET;")
        connection, factory = self.make_connection(FakeSocket(respond))
        for expected in (1, 2, 4):
            connection._retry_at = 0
            factory.return_value = FakeSocket(respond)
            connection.query("SRC;")
            with self.assertRaises(transport.TransportError):
                connection.query("VER;", timeout=.01)
            self.assertAlmostEqual(connection.diagnostics["retry_in_seconds"], expected, delta=.1)

    def test_stable_session_success_resets_failure_backoff(self):
        connection, _ = self.make_connection(FakeSocket(status_response))
        connection.query("SRC;")
        connection._failures = 5
        connection._connected_at = time.monotonic() - 61
        connection.query("SRC;")
        self.assertEqual(connection._failures, 0)

    def test_transaction_lock_wait_is_bounded(self):
        connection, _ = self.make_connection(FakeSocket())
        with connection.transaction():
            with ThreadPoolExecutor(max_workers=1) as pool:
                def blocked():
                    with connection.transaction(timeout=.01):
                        self.fail("Another transaction already owns the lock")
                with self.assertRaisesRegex(transport.TransportError, "busy"):
                    pool.submit(blocked).result(timeout=1)


if __name__ == "__main__":
    unittest.main()
