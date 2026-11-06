"""ACK identity/lifetime and actual server reader integration, without models."""
import json
import select
import socket
import struct
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid

from session_runtime import CommandSession
from test_command_states import Endpoint, FRAME, StopServer, load_function


class AckTests(unittest.TestCase):
    def start_response(self, session=None, hold_eos=False):
        conn = Mock()
        session = session or CommandSession(conn, Mock())
        self.addCleanup(session.close)
        started, eos, permit, done = (threading.Event() for _ in range(4))
        self.addCleanup(permit.set)
        tokens, outcomes = [], []
        def send(data):
            if data[0] == 0x13: eos.set()
        if session.conn is conn: conn.sendall.side_effect = send
        else: session.conn.sendall.side_effect = send
        def process(writer, *args, **kwargs):
            try:
                token = writer.begin_playback()
                tokens.append(token)
                started.set()
                if hold_eos and not permit.wait(2): raise RuntimeError('test timeout')
                writer.finish_playback(token)
                outcomes.append('completed')
            except Exception as error:
                outcomes.append(type(error).__name__)
            finally:
                done.set()
        session.state['mode'] = 'PROCESSING'
        session.start(process, FRAME)
        self.assertTrue(started.wait(2))
        if not hold_eos: self.assertTrue(eos.wait(2))
        return session, tokens[0], permit, eos, done, outcomes

    def finish(self, session, done):
        self.assertTrue(done.wait(2))
        session._worker.join(2)
        self.assertFalse(session._worker.is_alive())

    def test_processing_and_worker_do_not_complete_on_stop_send(self):
        session, token, _, _, done, outcomes = self.start_response()
        session.poll()
        self.assertEqual(session.state['mode'], 'PROCESSING')
        self.assertFalse(done.wait(0.02))
        self.assertTrue(session.receive_playback_ack(token))
        self.finish(session, done)
        self.assertEqual(outcomes, ['completed'])
        session.poll()
        self.assertEqual(session.state['mode'], 'WAKE_WORD')

    def test_early_wrong_and_duplicate_ack_do_not_complete_new_response(self):
        session, old, permit, eos, done, _ = self.start_response(hold_eos=True)
        self.assertFalse(session.receive_playback_ack(old))
        permit.set()
        self.assertTrue(eos.wait(2))
        self.assertFalse(session.receive_playback_ack(b'x' * 32))
        self.assertFalse(session.receive_playback_ack(b'x'))
        self.assertFalse(done.wait(0.02))
        session.receive_playback_ack(old)
        self.finish(session, done)
        session.poll()
        session, new, _, _, done, outcomes = self.start_response(session)
        self.assertNotEqual(old, new)
        self.assertFalse(session.receive_playback_ack(old))
        self.assertFalse(done.wait(0.02))
        session.receive_playback_ack(new)
        self.finish(session, done)
        self.assertEqual(outcomes, ['completed'])

    def test_disconnect_wakes_ack_wait_as_error(self):
        session, token, _, _, done, outcomes = self.start_response()
        session.close()
        self.finish(session, done)
        self.assertEqual(outcomes, ['ConnectionAbortedError'])
        self.assertFalse(session.receive_playback_ack(token))

    def test_old_connection_ack_cannot_complete_reconnected_response(self):
        old_session, old, _, _, done, _ = self.start_response()
        old_session.close()
        self.finish(old_session, done)
        new_session, new, _, _, done, outcomes = self.start_response()
        self.assertNotEqual(old[:16], new[:16])
        self.assertFalse(new_session.receive_playback_ack(old))
        self.assertFalse(done.wait(0.02))
        new_session.receive_playback_ack(new)
        self.finish(new_session, done)
        self.assertEqual(outcomes, ['completed'])

    def test_ack_timeout_invalidates_socket_and_releases_worker(self):
        with patch('session_runtime.PLAYBACK_ACK_TIMEOUT', 0.03):
            session, token, _, _, done, outcomes = self.start_response()
            self.finish(session, done)
        self.assertEqual(outcomes, ['TimeoutError'])
        session.conn.shutdown.assert_called_once()
        session.conn.close.assert_called_once()
        self.assertFalse(session.receive_playback_ack(token))

    def test_stop_send_failure_never_waits_for_success_ack(self):
        session, token, permit, eos, done, outcomes = self.start_response(hold_eos=True)
        session.conn.sendall.side_effect = OSError('disconnect during STOP')
        permit.set()
        self.finish(session, done)
        self.assertEqual(outcomes, ['OSError'])
        self.assertFalse(session.receive_playback_ack(token))

    def test_non_writer_cannot_start_or_finish_playback(self):
        session = CommandSession(Mock(), Mock())
        self.addCleanup(session.close)
        with self.assertRaises(RuntimeError): session.begin_playback()
        with self.assertRaises(RuntimeError): session.finish_playback(bytes(32))
        self.assertIsNone(session._playback_token)


class ServerAckIntegrationTests(unittest.TestCase):
    def launch(self):
        conn, peer = socket.socketpair()
        peer.settimeout(2)
        self.addCleanup(peer.close)
        listener, sessions = Endpoint(), []
        accepted = False
        def accept():
            nonlocal accepted
            if accepted: raise StopServer()
            accepted = True
            return conn, ('offline', 0)
        listener.accept = accept
        class ObservedSession(CommandSession):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                sessions.append(self)
        completed = threading.Event()
        def process(writer, *args, **kwargs):
            token = writer.begin_playback()
            writer.sendall(b'\x12\0\0\0\x04abcd')
            writer.finish_playback(token)
            completed.set()
        log = Mock()
        receive = load_function('recvall', dict(time=time, select=select, log=log))
        run = load_function('start_server', dict(
            socket=NS(socket=lambda *a: listener, AF_INET=0, SOCK_STREAM=0, SOL_SOCKET=0, SO_REUSEADDR=0),
            webrtcvad=NS(Vad=lambda _: NS(is_speech=lambda *a: False)),
            np=NS(frombuffer=lambda *a, **k: [], int16=None, abs=lambda x, **kwargs: x, max=lambda x: 0),
            wake_recognizer=NS(AcceptWaveform=lambda _: False,
                               PartialResult=lambda: json.dumps({'partial':'декстер'}), Reset=lambda: None),
            log=log, HOST='', PORT=3333, recvall=receive, struct=struct, json=json, uuid=uuid,
            threading=threading, CommandSession=ObservedSession, process_command=process))
        def serve():
            try: run()
            except StopServer: pass
        server = threading.Thread(target=serve, daemon=True)
        server.start()
        self.addCleanup(lambda: (peer.close(), conn.close(), server.join(2)))
        for _ in range(2):
            chunk = FRAME * 26
            peer.sendall(b'\x02' + struct.pack('>I', len(chunk)) + chunk)
        def exact(n):
            data = bytearray()
            while len(data) < n:
                part = peer.recv(n-len(data))
                self.assertTrue(part)
                data.extend(part)
            return bytes(data)
        self.assertEqual(exact(5), b'\x11\0\0\0\x20')
        token = exact(32)
        self.assertEqual(exact(9), b'\x12\0\0\0\x04abcd')
        self.assertEqual(exact(5), b'\x13\0\0\0\x20')
        self.assertEqual(exact(32), token)
        return peer, sessions[0], token, completed

    def test_real_receiver_dispatches_fragmented_ack_while_consuming_mic(self):
        peer, session, token, completed = self.launch()
        peer.sendall(b'\x14\0\0\0\x20' + b'x' * 32)
        peer.sendall(b'\x02\0\0\x03\xc0' + FRAME)
        self.assertFalse(completed.wait(0.03))
        self.assertEqual(session.state['mode'], 'PROCESSING')
        frame = b'\x14\0\0\0\x20' + token
        peer.sendall(frame[:2])
        peer.sendall(frame[2:18])
        peer.sendall(frame[18:])
        self.assertTrue(completed.wait(2))

    def test_malformed_ack_length_closes_connection_and_wakes_worker(self):
        peer, session, _, completed = self.launch()
        peer.sendall(b'\x14\0\0\0\x1f' + bytes(31))
        self.assertTrue(session._cancelled.wait(2))
        self.assertFalse(completed.is_set())
        session._worker.join(2)
        self.assertFalse(session._worker.is_alive())


if __name__ == '__main__':
    unittest.main()
