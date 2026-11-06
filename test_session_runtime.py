"""Offline ownership/concurrency tests. No models, SDKs or external network."""
import re
import socket
import struct
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, Mock

from session_runtime import CommandSession
from dialog_memory import DialogMemory
from test_command_states import FRAME, load_function


class SessionTests(unittest.TestCase):
    def session(self):
        conn = Mock()
        session = CommandSession(conn, Mock())
        session.state.update(mode='PROCESSING', session_id='same-short-id')
        self.addCleanup(session.close)
        return session, conn

    def wait(self, event):
        self.assertTrue(event.wait(2), 'worker did not reach the expected checkpoint')

    def join(self, session):
        session._worker.join(2)
        self.assertFalse(session._worker.is_alive(), 'worker failed to stop')

    def pipeline(self, history=None):
        pcm = NS(tobytes=lambda: FRAME)
        stt = Mock(return_value=NS(text='new command'))
        llm = Mock(return_value=NS(choices=[NS(message=NS(content='reply'))]))
        tts = Mock(return_value=True)
        namespace = dict(np=NS(int16=None, frombuffer=lambda *a, **k: pcm,
                               max=lambda _: 0, abs=lambda x, **kwargs: x),
                         log=Mock(), wave=NS(open=MagicMock()), open=MagicMock(),
                         time=NS(time=lambda: 0), dialog_memory=history if history is not None else DialogMemory(),
                         os=NS(path=NS(exists=lambda _: True), remove=Mock()),
                         groq_client=NS(audio=NS(transcriptions=NS(create=stt)),
                                        chat=NS(completions=NS(create=llm))),
                         send_audio_to_esp32=tts)
        return load_function('process_command', namespace), namespace, stt, llm, tts

    def test_duplicate_start_is_rejected_and_receiver_owns_mode(self):
        session, _ = self.session()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def process(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise RuntimeError('test checkpoint timeout')
        session.start(process, FRAME)
        self.wait(entered)
        with self.assertRaises(RuntimeError):
            session.start(process, FRAME)
        release.set()
        self.join(session)
        self.assertEqual(session.state['mode'], 'PROCESSING')
        session.poll()
        self.assertEqual(session.state['mode'], 'WAKE_WORD')

    def test_reconnect_serializes_pipeline_and_discards_stale_stt_result(self):
        old, old_conn = self.session()
        new, _ = self.session()
        history = DialogMemory()
        process1, env1, stt1, llm1, tts1 = self.pipeline(history)
        process2, env2, stt2, _, tts2 = self.pipeline(history)
        entered, release = threading.Event(), threading.Event()
        new_entered = threading.Event()
        self.addCleanup(release.set)
        def delayed_stt(**kwargs):
            entered.set()
            if not release.wait(2):
                raise RuntimeError('test checkpoint timeout')
            return NS(text='obsolete')
        stt1.side_effect = delayed_stt
        stt2.side_effect = lambda **kwargs: (new_entered.set(), NS(text='new command'))[1]
        old.start(process1, FRAME)
        self.wait(entered)
        old.close()
        new.start(process2, FRAME)
        self.assertFalse(new_entered.wait(0.05))
        release.set()
        self.join(old)
        self.join(new)
        self.assertTrue(new_entered.is_set())
        llm1.assert_not_called()
        tts1.assert_not_called()
        tts2.assert_called_once()
        old_conn.sendall.assert_not_called()
        self.assertEqual(history.prepare('', '')[0][1:-1], [{'role': 'user', 'content': 'new command'},
                                   {'role': 'assistant', 'content': 'reply'}])
        first_path = env1['wave'].open.call_args.args[0]
        second_path = env2['wave'].open.call_args.args[0]
        self.assertNotEqual(first_path, second_path)
        env1['os'].remove.assert_called_once_with(first_path)
        env2['os'].remove.assert_called_once_with(second_path)
        new.poll()
        self.assertEqual(new.state['mode'], 'WAKE_WORD')

    def test_disconnect_during_llm_does_not_commit_history_or_synthesize(self):
        session, _ = self.session()
        process, env, _, llm, tts = self.pipeline()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def delayed_llm(**kwargs):
            entered.set()
            if not release.wait(2):
                raise RuntimeError('test checkpoint timeout')
            return NS(choices=[NS(message=NS(content='obsolete'))])
        llm.side_effect = delayed_llm
        session.start(process, FRAME)
        self.wait(entered)
        session.close()
        release.set()
        self.join(session)
        self.assertEqual(env['dialog_memory'].prepare('', '')[0][1:-1], [])
        tts.assert_not_called()

    def test_disconnected_queued_worker_never_calls_pipeline(self):
        first, _ = self.session()
        queued, _ = self.session()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def blocked(*args, **kwargs):
            entered.set()
            release.wait(2)
        first.start(blocked, FRAME)
        self.wait(entered)
        process = Mock()
        queued.start(process, FRAME)
        queued.close()
        self.join(queued)
        process.assert_not_called()
        release.set()
        self.join(first)

    def test_only_active_worker_can_write_and_disconnect_rejects_commit(self):
        session, conn = self.session()
        with self.assertRaises(RuntimeError):
            session.sendall(b'forbidden')
        conn.sendall.assert_not_called()
        session.close()
        action = Mock()
        with self.assertRaises(ConnectionAbortedError):
            session.commit(action)
        action.assert_not_called()
        with self.assertRaises(ConnectionAbortedError):
            session.start(Mock(), FRAME)

    def test_send_error_invalidates_connection_and_unblocks_receiver(self):
        session, conn = self.session()
        conn.sendall.side_effect = OSError('partial write/disconnect')
        def process(writer, *args, **kwargs):
            writer.sendall(b'header')
        session.start(process, FRAME)
        self.join(session)
        conn.shutdown.assert_called_once_with(socket.SHUT_RDWR)
        conn.close.assert_called_once()
        with self.assertRaises(ConnectionAbortedError):
            session.sendall(b'next')
        self.assertEqual(conn.sendall.call_count, 1)

    def test_close_unblocks_real_socket_receiver(self):
        conn, peer = socket.socketpair()
        self.addCleanup(peer.close)
        session = CommandSession(conn, Mock())
        self.addCleanup(session.close)
        entered, stopped = threading.Event(), threading.Event()
        def receive():
            entered.set()
            try:
                conn.recv(5)
            except OSError:
                pass
            finally:
                stopped.set()
        receiver = threading.Thread(target=receive, daemon=True)
        receiver.start()
        self.wait(entered)
        session.close()
        self.wait(stopped)
        receiver.join(2)

    def test_repeated_command_ids_have_unique_temp_names(self):
        session, _ = self.session()
        names = []
        def process(writer, audio, state, **kwargs):
            names.append(state['session_id'])
        for _ in range(2):
            session.start(process, FRAME)
            self.join(session)
            session.poll()
            session.state.update(mode='PROCESSING', session_id='same-short-id')
        self.assertEqual(len(set(names)), 2)

    def test_errors_release_pipeline_lock_and_restore_receiver(self):
        for stage in ('stt', 'llm', 'tts', 'unexpected'):
            with self.subTest(stage=stage):
                session, _ = self.session()
                process, env, stt, llm, tts = self.pipeline()
                if stage == 'unexpected':
                    process = Mock(side_effect=RuntimeError('unexpected'))
                else:
                    {'stt': stt, 'llm': llm, 'tts': tts}[stage].side_effect = RuntimeError(stage)
                session.start(process, FRAME)
                self.join(session)
                session.poll()
                self.assertEqual(session.state['mode'], 'WAKE_WORD')
                next_process = Mock()
                session.state['mode'] = 'PROCESSING'
                session.start(next_process, FRAME)
                self.join(session)
                next_process.assert_called_once()

    def test_old_worker_never_writes_to_reconnected_socket(self):
        old, old_conn = self.session()
        new, new_conn = self.session()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def old_process(writer, *args, **kwargs):
            entered.set()
            release.wait(2)
            writer.sendall(b'old')
        old.start(old_process, FRAME)
        self.wait(entered)
        old.close()
        new.start(lambda writer, *a, **k: writer.sendall(b'new'), FRAME)
        release.set()
        self.join(old)
        self.join(new)
        old_conn.sendall.assert_not_called()
        new_conn.sendall.assert_called_once_with(b'new')

    def test_actual_tts_sender_preserves_frames_and_pcm(self):
        # Actual sender AST and real OS socketpair; synthetic model/PCM only.
        import numpy as np
        samples = np.full(4500, 1.0 / 32767, dtype=np.float64)
        model = NS(apply_tts=Mock(return_value=NS(numpy=lambda: samples)))
        namespace = dict(log=Mock(), time=NS(time=lambda: 0, sleep=lambda _: None),
                         re=re, np=np, silero_model=model, struct=struct)
        sender = load_function('send_audio_to_esp32', namespace)
        writer, reader = socket.socketpair()
        reader.settimeout(2)
        self.addCleanup(reader.close)
        session = CommandSession(writer, Mock())
        self.addCleanup(session.close)
        results = []
        session.start(lambda conn, *a, **k: results.append(sender(conn, 'Test.')), FRAME)
        expected = b'\x01\x00' * 4500 + bytes(12000)
        def exact(n):
            buf = bytearray()
            while len(buf) < n:
                part = reader.recv(n - len(buf))
                self.assertTrue(part)
                buf.extend(part)
            return bytes(buf)
        self.assertEqual(exact(5), b'\x11\0\0\0\x20')
        token = exact(32)
        pcm, sizes = bytearray(), []
        while True:
            kind, size = struct.unpack('>BI', exact(5))
            payload = exact(size)
            if kind == 0x13:
                self.assertEqual(payload, token)
                break
            self.assertEqual(kind, 0x12)
            sizes.append(size)
            pcm.extend(payload)
        self.assertEqual(results, [])
        self.assertTrue(session.receive_playback_ack(token))
        self.join(session)
        self.assertEqual(pcm, expected)
        self.assertEqual(sizes, [4096] * 5 + [520])
        self.assertEqual(results, [True])
        model.apply_tts.assert_called_once_with(text='Test.', speaker='aidar', sample_rate=24000)


if __name__ == '__main__':
    unittest.main()
