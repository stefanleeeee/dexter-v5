"""Cancellation ownership, stage fences and local control; no real models/API."""
import io
import re
import struct
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from cancel_control import CancelControl
from session_runtime import CommandSession
from test_command_states import FRAME, load_function
import test_session_runtime
import test_playback_ack


class CancelTests(unittest.TestCase):
    def cleanup_session(self, session, release):
        release.set()
        session.close()
        if session._worker is not None: session._worker.join(2)

    def launch(self, stage='ack', session=None):
        session = session or CommandSession(Mock(), Mock())
        entered, eos, cancel, release = (threading.Event() for _ in range(4))
        self.addCleanup(self.cleanup_session, session, release)
        frames, tokens, completed = [], [], []
        def send(data):
            frames.append(bytes(data))
            if (stage == 'send' and data[0] == 0x12) or (stage == 'start' and data[0] == 0x11):
                entered.set()
                release.wait(2)
            if data[0] == 0x13: eos.set()
            if data[0] == 0x15: cancel.set()
        session.conn.sendall.side_effect = send
        def process(writer, *args, **kwargs):
            if stage == 'generation':
                entered.set()
                release.wait(2)
                writer.check_active()
            token = writer.begin_playback()
            tokens.append(token)
            writer.sendall(b'\x12\0\0\0\x04abcd')
            writer.finish_playback(token)
            completed.append(True)
        session.state['mode'] = 'PROCESSING'
        session.start(process, FRAME)
        self.assertTrue((entered if stage in ('generation', 'send', 'start') else eos).wait(2))
        return NS(session=session, entered=entered, eos=eos, cancel=cancel, release=release,
                  frames=frames, tokens=tokens, completed=completed)

    def join(self, session):
        self.assertTrue(session._done.wait(2))
        session._worker.join(2)
        self.assertFalse(session._worker.is_alive())

    def confirm_cancel(self, job):
        self.assertTrue(job.cancel.wait(2))
        token = job.tokens[0]
        self.assertFalse(job.session.receive_playback_ack(token))
        self.assertFalse(job.session.receive_cancel_ack(b'x' * 32))
        self.assertTrue(job.session.receive_cancel_ack(token))
        self.join(job.session)
        self.assertEqual(job.completed, [])
        self.assertFalse(any('PLAYBACK DONE' in str(c) for c in job.session.log.call_args_list))

    def test_cancel_before_audio_suppresses_late_generation(self):
        job = self.launch('generation')
        self.assertTrue(job.session.request_cancel(job.session.command_id))
        job.release.set()
        self.join(job.session)
        self.assertEqual(job.frames, [])
        job.session.poll()
        self.assertEqual(job.session.state['mode'], 'WAKE_WORD')

    def test_cancel_during_frame_finishes_frame_then_sends_cancel(self):
        job = self.launch('send')
        self.assertTrue(job.session.request_cancel(job.tokens[0]))
        job.release.set()
        self.confirm_cancel(job)
        self.assertEqual([frame[0] for frame in job.frames], [0x11, 0x12, 0x15])
        for frame in job.frames:
            self.assertEqual(struct.unpack('>I', frame[1:5])[0], len(frame) - 5)

    def test_cancel_during_start_sends_cancel_for_that_exact_started_id(self):
        job = self.launch('start')
        token = job.frames[0][5:]
        self.assertTrue(job.session.request_cancel())
        job.release.set()
        self.assertTrue(job.cancel.wait(2))
        self.assertTrue(job.session.receive_cancel_ack(token))
        self.join(job.session)
        self.assertEqual([frame[0] for frame in job.frames], [0x11, 0x15])
        self.assertEqual(job.frames[-1][5:], token)
        self.assertEqual(job.completed, [])

    def test_cancel_after_id_assignment_but_before_start_does_not_send_unknown_id(self):
        session = CommandSession(Mock(), Mock())
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(self.cleanup_session, session, release)
        original = session.check_active
        paused = False
        def check():
            nonlocal paused
            if session._playback_token is not None and not session._playback_started and not paused:
                paused = True
                entered.set()
                release.wait(2)
            original()
        session.check_active = check
        session.start(lambda writer, *a, **k: writer.begin_playback(), FRAME)
        self.assertTrue(entered.wait(2))
        self.assertTrue(session.request_cancel())
        release.set()
        self.join(session)
        session.conn.sendall.assert_not_called()
        self.assertFalse(session._cancelled.is_set())
        self.assertIsNone(session._playback_token)

    def test_cancel_ack_wait_rejects_normal_ack_and_is_idempotent(self):
        job = self.launch()
        self.assertFalse(job.session.receive_cancel_ack(job.tokens[0]))
        self.assertTrue(job.session.request_cancel())
        self.assertTrue(job.session.request_cancel())
        self.confirm_cancel(job)
        self.assertEqual(sum(frame[0] == 0x15 for frame in job.frames), 1)
        self.assertFalse(job.session._cancelled.is_set())

    def test_stale_cancel_and_timer_cannot_affect_next_command(self):
        old = self.launch()
        old_command = old.session.command_id
        old.session.request_cancel()
        self.confirm_cancel(old)
        old.session.poll()
        new = self.launch(session=old.session)
        self.assertNotEqual(old.tokens[0], new.tokens[0])
        self.assertFalse(new.session.request_cancel(old_command))
        self.assertFalse(new.session.request_cancel(old.tokens[0]))
        self.assertFalse(new.session.receive_cancel_ack(old.tokens[0]))
        new.session._abort_stuck_cancel(old_command)
        self.assertFalse(new.session._cancelled.is_set())
        self.assertTrue(new.session.receive_playback_ack(new.tokens[0]))
        self.join(new.session)
        self.assertEqual(new.completed, [True])

    def test_normal_ack_commit_wins_against_later_cancel(self):
        job = self.launch()
        job.session.receive_playback_ack(job.tokens[0])
        self.join(job.session)
        self.assertFalse(job.session.request_cancel())
        self.assertEqual(job.completed, [True])
        self.assertFalse(any(frame[0] == 0x15 for frame in job.frames))

    def test_missing_cancel_ack_invalidates_connection_without_success(self):
        with patch('session_runtime.CANCEL_TIMEOUT', 0.05):
            job = self.launch()
            job.session.request_cancel()
            self.assertTrue(job.cancel.wait(2))
            self.join(job.session)
        self.assertTrue(job.session._cancelled.is_set())
        self.assertEqual(job.completed, [])
        self.assertFalse(any('[PLAYBACK CANCELLED]' in str(c) for c in job.session.log.call_args_list))

    def test_blocked_writer_has_abortive_close_fallback(self):
        with patch('session_runtime.CANCEL_TIMEOUT', 0.05):
            job = self.launch('send')
            job.session.conn.shutdown.side_effect = lambda *a: job.release.set()
            job.session.request_cancel()
            self.join(job.session)
        self.assertTrue(job.session._cancelled.is_set())
        job.session.conn.setsockopt.assert_called_once()
        self.assertEqual(job.completed, [])

    def test_disconnect_during_cancellation_never_confirms_success(self):
        job = self.launch()
        job.session.request_cancel()
        self.assertTrue(job.cancel.wait(2))
        job.session.close()
        self.join(job.session)
        self.assertFalse(job.session.receive_cancel_ack(job.tokens[0]))
        self.assertEqual(job.completed, [])
        new = self.launch()
        self.assertFalse(new.session.receive_cancel_ack(job.tokens[0]))
        new.session.receive_playback_ack(new.tokens[0])
        self.join(new.session)

    def test_stt_and_llm_cancellation_do_not_commit_late_results(self):
        for stage in ('stt', 'llm'):
            with self.subTest(stage=stage):
                session = CommandSession(Mock(), Mock())
                entered, release = threading.Event(), threading.Event()
                self.addCleanup(self.cleanup_session, session, release)
                process, env, stt, llm, tts = test_session_runtime.SessionTests().pipeline()
                original = stt.return_value if stage == 'stt' else llm.return_value
                def delayed(**kwargs):
                    entered.set()
                    release.wait(2)
                    return original
                (stt if stage == 'stt' else llm).side_effect = delayed
                session.start(process, FRAME)
                self.assertTrue(entered.wait(2))
                self.assertTrue(session.request_cancel())
                release.set()
                self.join(session)
                self.assertEqual(env['dialog_memory'].prepare('', '')[0][1:-1], [])
                tts.assert_not_called()
                if stage == 'stt': llm.assert_not_called()
                session.conn.sendall.assert_not_called()

    def test_tts_cancellation_between_sentences_suppresses_all_audio(self):
        session = CommandSession(Mock(), Mock())
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(self.cleanup_session, session, release)
        def synthesize(**kwargs):
            entered.set()
            release.wait(2)
            return NS(numpy=lambda: [0.0])
        model = NS(apply_tts=Mock(side_effect=synthesize))
        sender = load_function('send_audio_to_esp32', dict(log=Mock(), time=NS(time=lambda: 0),
                                re=re, silero_model=model, np=Mock()))
        session.start(lambda writer, *a, **k: sender(writer, 'One. Two.'), FRAME)
        self.assertTrue(entered.wait(2))
        session.request_cancel()
        release.set()
        self.join(session)
        self.assertEqual(model.apply_tts.call_count, 1)
        session.conn.sendall.assert_not_called()

    def test_console_commands_use_current_command_id_and_ignore_other_input(self):
        log, session = Mock(), Mock()
        session.command_id = 'generation_command'
        session.request_cancel.return_value = True
        control = CancelControl(log, io.StringIO('hello\n\ncancel\nОтмена\n'))
        control.attach(session)
        control.run()
        self.assertEqual(session.request_cancel.call_count, 2)
        session.request_cancel.assert_called_with(expected_id='generation_command')
        session.command_id = None
        session.cancel_followup.return_value = False
        self.assertFalse(control.cancel_current())

    def test_no_active_console_command_is_safe(self):
        control = CancelControl(Mock(), io.StringIO('cancel\n'))
        self.assertFalse(control.cancel_current())
        control.run()


class CancelReaderIntegrationTests(unittest.TestCase):
    def launch(self):
        case = test_playback_ack.ServerAckIntegrationTests()
        self.addCleanup(case.doCleanups)
        peer, session, token, completed = case.launch()
        worker = session._worker
        self.assertTrue(session.request_cancel(token))
        def exact(n):
            data = bytearray()
            while len(data) < n:
                part = peer.recv(n-len(data))
                self.assertTrue(part)
                data.extend(part)
            return bytes(data)
        self.assertEqual(exact(37), b'\x15\0\0\0\x20' + token)
        return peer, session, token, completed, worker, exact

    def test_actual_reader_dispatches_cancelled_and_next_reply_still_works(self):
        peer, session, token, completed, worker, exact = self.launch()
        peer.sendall(b'\x14\0\0\0\x20' + token)  # Normal DONE loses to accepted cancellation.
        peer.sendall(b'\x16\0\0\0\x20' + b'x' * 32)
        self.assertFalse(completed.wait(0.02))
        frame = b'\x16\0\0\0\x20' + token
        peer.sendall(frame[:3])
        peer.sendall(frame[3:])
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(completed.is_set())
        self.assertFalse(session._cancelled.is_set())
        for _ in range(2):
            chunk = FRAME * 26
            peer.sendall(b'\x02' + struct.pack('>I', len(chunk)) + chunk)
        self.assertEqual(exact(5), b'\x11\0\0\0\x20')
        new = exact(32)
        self.assertNotEqual(new, token)
        self.assertEqual(new[:16], token[:16])
        self.assertEqual(exact(9), b'\x12\0\0\0\x04abcd')
        self.assertEqual(exact(37), b'\x13\0\0\0\x20' + new)
        peer.sendall(b'\x16\0\0\0\x20' + token)  # Stale cancellation ACK cannot finish reply 2.
        self.assertFalse(completed.wait(0.02))
        peer.sendall(b'\x14\0\0\0\x20' + new)
        self.assertTrue(completed.wait(2))

    def test_reset_like_socket_loss_during_cancel_has_no_success(self):
        peer, session, _, completed, worker, _ = self.launch()
        peer.close()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(session._cancelled.is_set())
        self.assertFalse(completed.is_set())
        self.assertFalse(any('[PLAYBACK CANCELLED]' in str(c) for c in session.log.call_args_list))

    def test_malformed_cancelled_ack_closes_stream(self):
        peer, session, _, completed, worker, _ = self.launch()
        peer.sendall(b'\x16\0\0\0\x1f' + bytes(31))
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(session._cancelled.is_set())
        self.assertFalse(completed.is_set())


if __name__ == '__main__':
    unittest.main()
