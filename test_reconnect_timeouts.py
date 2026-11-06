"""Server-side frame deadlines and stalled-writer tests without models/API."""
import socket
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from session_runtime import CommandSession
from test_command_states import load_function


class ReceiveDeadlineTests(unittest.TestCase):
    def receive(self, fragments, advances=None, ready=True):
        clock = [0.0]
        chunks = iter(fragments)
        steps = iter(advances or [0.0] * len(fragments))
        conn = Mock()
        def recv(size):
            clock[0] += next(steps, 0.0)
            chunk = next(chunks)
            if isinstance(chunk, Exception): raise chunk
            return chunk
        conn.recv.side_effect = recv
        log = Mock()
        select = Mock(return_value=([conn], [], []) if ready else ([], [], []))
        function = load_function('recvall', dict(time=NS(monotonic=lambda: clock[0]),
                                 select=NS(select=select), log=log))
        return function, conn, select, log

    def test_fragmented_frame_is_complete_before_deadline(self):
        function, conn, select, _ = self.receive([b'ab', b'cd'], [2.0, 3.0])
        self.assertEqual(function(conn, 4), b'abcd')
        self.assertEqual([c.args[3] for c in select.call_args_list], [10.0, 8.0])
        conn.settimeout.assert_not_called()  # Shared writer timeout is untouched.

    def test_slow_trickle_cannot_reset_body_deadline(self):
        function, conn, _, _ = self.receive([b'a'] * 4, [3.0] * 4)
        self.assertIsNone(function(conn, 4))

    def test_last_fragment_at_deadline_is_rejected(self):
        function, conn, _, _ = self.receive([b'ab'], [10.0])
        self.assertIsNone(function(conn, 2))

    def test_no_readiness_returns_failure_without_recv(self):
        function, conn, _, _ = self.receive([], ready=False)
        self.assertIsNone(function(conn, 5))
        conn.recv.assert_not_called()

    def test_eof_partial_header_and_socket_error(self):
        for fragments in ([b'a', b''], [OSError('disconnect')], [TimeoutError('timeout')]):
            with self.subTest(fragments=fragments):
                function, conn, _, _ = self.receive(fragments)
                self.assertIsNone(function(conn, 5))

    def test_empty_read_is_noop(self):
        function, conn, select, _ = self.receive([])
        self.assertEqual(function(conn, 0), b'')
        select.assert_not_called()
        conn.recv.assert_not_called()

    def test_stalled_actual_socket_writer_times_out_and_invalidates_session(self):
        writer, reader = socket.socketpair()
        self.addCleanup(reader.close)
        reader.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
        writer.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024)
        writer.settimeout(0.05)  # Shorten only this test's per-sendall deadline.
        session = CommandSession(writer, Mock())
        self.addCleanup(session.close)
        outcomes = []
        def process(conn, *args, **kwargs):
            try:
                # Small frames force ordinary socket backpressure; a single huge
                # send may be buffered optimistically by the Windows loopback path.
                for _ in range(4096):
                    conn.sendall(bytes(65536))
            except TimeoutError:
                outcomes.append('timeout')
        session.start(process, b'')
        session._worker.join(2)
        self.assertFalse(session._worker.is_alive())
        self.assertEqual(outcomes, ['timeout'])
        with self.assertRaises(ConnectionAbortedError):
            session.check_active()


if __name__ == '__main__':
    unittest.main()
