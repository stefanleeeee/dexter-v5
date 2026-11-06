"""Offline tests of actual server function ASTs; no server imports/models/network.

Run: python -B -m unittest -v test_command_states
DEXTER_TEST_SOURCE may select the rollback file for regression reproduction.
"""
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import struct
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock
import uuid
from session_runtime import CommandSession, CommandCancelled


SOURCE = Path(os.environ.get('DEXTER_TEST_SOURCE', Path(__file__).with_name('server.py')))
FRAME = b'\0' * 960


def load_function(name, namespace, command_limit=None):
    from dialog_memory import DialogMemory
    namespace.setdefault('dialog_memory', DialogMemory())
    namespace.setdefault('json', json)
    namespace.setdefault('CommandCancelled', CommandCancelled)
    from llm_options import completion_options
    namespace.setdefault('completion_options', completion_options)
    from tts_audio import synthesize_samples, encode_pcm
    namespace.setdefault('synthesize_samples', synthesize_samples)
    namespace.setdefault('encode_pcm', encode_pcm)
    from tts_text_normalizer import normalize_tts_text, remaining_latin_count
    namespace.setdefault('normalize_tts_text', normalize_tts_text)
    namespace.setdefault('remaining_latin_count', remaining_latin_count)
    namespace.setdefault('voice_settings', NS(snapshot=lambda: 'aidar'))
    namespace.setdefault('CONFIG', NS(tts_normalize_en=True))
    tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    if command_limit is not None:
        # Force the otherwise unreachable short-command branch, without changing production thresholds.
        for n in ast.walk(node):
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'MAX_COMMAND_FRAMES' for t in n.targets):
                n.value = ast.Constant(command_limit)
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(module, str(SOURCE), 'exec'), namespace)
    return namespace[name]


class StopServer(BaseException):
    pass


class Endpoint:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


class StateTests(unittest.TestCase):
    def run_stream(self, chunks, speech=False, command_limit=None, finish=False, start_error=False):
        listener, connection = Endpoint(), Endpoint()
        accepted = False
        jobs = []
        observations = []
        class ObservedSession(CommandSession):
            def __exit__(self, *args):
                # Preserve the receiver state at disconnect for the 1.1 assertions.
                for job in jobs:
                    job['args'] = (job['args'][0], job['args'][1], dict(job['args'][2]))
                super().__exit__(*args)
        recognizer = NS(AcceptWaveform=Mock(return_value=False),
                        PartialResult=Mock(return_value=json.dumps({'partial': 'декстер'})), Reset=Mock())

        def accept():
            nonlocal accepted
            if accepted:
                raise StopServer()
            accepted = True
            return connection, ('offline', 0)

        listener.accept = accept
        packets = []
        for chunk in chunks:
            packets.extend([b'\x02' + struct.pack('>I', len(chunk)), chunk])
        cursor = iter(packets)

        def receive(*args):
            if jobs:
                observations.append(jobs[-1]['args'][2]['mode'])
            return next(cursor, None)

        def thread_factory(**kwargs):
            session = kwargs['target'].__self__
            jobs.append(dict(kwargs, args=(connection, kwargs['args'][1], session.state)))

            def start():
                if start_error:
                    raise RuntimeError('offline thread start failure')
                if finish:
                    session._done.set()
            return NS(start=start)

        namespace = dict(socket=NS(socket=lambda *a: listener, AF_INET=0, SOCK_STREAM=0,
                                   SOL_SOCKET=0, SO_REUSEADDR=0),
                         webrtcvad=NS(Vad=lambda _: NS(is_speech=lambda *a: speech)),
                         np=NS(frombuffer=lambda *a, **k: [], int16=None, abs=lambda x, **kwargs: x,
                               max=lambda x: 2000 if speech else 0),
                         log=lambda *a: None, HOST='', PORT=3333, recvall=receive,
                         wake_recognizer=recognizer, json=json, uuid=uuid, struct=struct,
                         threading=NS(Thread=thread_factory), process_command=lambda *a: None,
                         CommandSession=ObservedSession)
        run = load_function('start_server', namespace, command_limit)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(StopServer):
            run()
        return jobs, observations, recognizer

    def test_unfinished_command_does_not_start(self):
        jobs, _, _ = self.run_stream([FRAME * 20])
        self.assertEqual(jobs, [])

    def test_silence_finishes_exactly_once(self):
        jobs, states, _ = self.run_stream([FRAME * 26, FRAME * 26, FRAME * 20, FRAME * 20])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(len(jobs[0]['args'][1]), 51 * 960)
        self.assertEqual(jobs[0]['args'][2]['mode'], 'PROCESSING')
        self.assertTrue(states)
        self.assertEqual(set(states), {'PROCESSING'})

    def test_remaining_frames_do_not_reactivate_wake_word(self):
        jobs, _, recognizer = self.run_stream([FRAME * 26, FRAME * 30])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['args'][2]['mode'], 'PROCESSING')
        self.assertEqual(recognizer.AcceptWaveform.call_count, 1)

    def test_max_duration_finishes_once(self):
        jobs, _, _ = self.run_stream([FRAME * 30] * 10, speech=True)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(len(jobs[0]['args'][1]), 234 * 960)

    def test_short_completed_command_does_not_start(self):
        jobs, _, _ = self.run_stream([FRAME * 5], command_limit=0)
        self.assertEqual(jobs, [])

    def test_fast_worker_discards_old_payload_tail_including_partial_frame(self):
        jobs, _, recognizer = self.run_stream([FRAME * 26, FRAME * 30 + FRAME[:100], FRAME[:860]], finish=True)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(recognizer.AcceptWaveform.call_count, 1)

    def test_next_command_after_completion_starts_once(self):
        jobs, _, _ = self.run_stream([FRAME * 26, FRAME * 26] * 2, finish=True)
        self.assertEqual(len(jobs), 2)
        self.assertNotEqual(jobs[0]['args'][2].get('session_id'), None)
        self.assertEqual([len(j['args'][1]) for j in jobs], [51 * 960] * 2)

    def test_thread_start_failure_restores_wake_word(self):
        jobs, _, _ = self.run_stream([FRAME * 26, FRAME * 26], start_error=True)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['args'][2]['mode'], 'WAKE_WORD')

    def test_processing_failure_restores_state_and_cleans_temp_file(self):
        for stage in ('dsp', 'stt', 'llm', 'tts', 'success'):
            with self.subTest(stage=stage):
                state = {'mode': 'PROCESSING', 'session_id': 'offline'}
                failure = RuntimeError('offline injected failure')
                pcm = NS(tobytes=lambda: FRAME)
                np = NS(int16=None, frombuffer=Mock(return_value=pcm), max=lambda _: 0, abs=lambda x, **kwargs: x)
                if stage == 'dsp':
                    np.frombuffer.side_effect = failure
                stt = Mock(return_value=NS(text='test'))
                llm = Mock(return_value=NS(choices=[NS(message=NS(content='reply'))]))
                tts = Mock(return_value=True)
                if stage in ('stt', 'llm', 'tts'):
                    {'stt': stt, 'llm': llm, 'tts': tts}[stage].side_effect = failure
                remove = Mock()
                namespace = dict(np=np, log=lambda *a: None, wave=NS(open=Mock()),
                                 open=Mock(), time=NS(time=lambda: 0), 
                                 os=NS(path=NS(exists=lambda _: True), remove=remove),
                                 groq_client=NS(audio=NS(transcriptions=NS(create=stt)),
                                                chat=NS(completions=NS(create=llm))),
                                 send_audio_to_esp32=tts)
                # Context managers for WAV and file objects, with no disk access.
                from unittest.mock import MagicMock
                namespace['wave'].open.return_value = MagicMock()
                namespace['open'].return_value = MagicMock()
                process = load_function('process_command', namespace)
                if stage == 'dsp':
                    with self.assertRaisesRegex(RuntimeError, 'offline injected'):
                        process(None, FRAME, state)
                else:
                    process(None, FRAME, state)
                self.assertEqual(state['mode'], 'WAKE_WORD')
                remove.assert_called_once_with('upload_temp_offline.wav')
                # The restored mode accepts a fresh command in the loop tests above.


if __name__ == '__main__':
    unittest.main()
