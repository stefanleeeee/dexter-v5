"""Task 1.8: real NumPy, isolated server AST; no models or external API."""
import ast
import os
import re
import struct
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, MagicMock
import numpy as np
import test_command_states as states


class PCMEdges(unittest.TestCase):
    def sender(self, output=None, failure=False):
        calls = []
        def synth(text, **kwargs):
            calls.append(text)
            if failure: raise RuntimeError('injected TTS failure')
            return NS(numpy=lambda: np.array([0.25]) if output is None else output)
        conn = Mock()
        sender = states.load_function('send_audio_to_esp32', dict(
            np=np, re=re, struct=struct, log=Mock(), silero_model=NS(apply_tts=synth),
            time=NS(time=lambda: 0, sleep=lambda _: None)))
        return sender, conn, calls

    def test_empty_text_has_no_model_or_wire_activity(self):
        for text in ('', '  \n '):
            sender, conn, calls = self.sender()
            self.assertTrue(sender(conn, text))
            self.assertEqual(calls, [])
            conn.begin_playback.assert_not_called()
            conn.sendall.assert_not_called()

    def test_long_sentence_preserves_words_and_bounded_calls(self):
        text = ' '.join('word%04d' % i for i in range(550)) + '.'
        sender, conn, calls = self.sender()
        self.assertTrue(sender(conn, text))
        self.assertEqual(' '.join(calls), text)
        self.assertTrue(all(0 < len(x) <= 900 for x in calls))
        conn.finish_playback.assert_called_once()

    def test_unbroken_text_preserved_at_limit_boundaries(self):
        for length in (899, 900, 901, 1800, 1801):
            sender, conn, calls = self.sender()
            text = 'x' * length
            self.assertTrue(sender(conn, text))
            self.assertEqual(''.join(calls), text)
            self.assertTrue(all(len(x) <= 900 for x in calls))

    def test_mixed_sentences_keep_last_word(self):
        sender, conn, calls = self.sender()
        text = 'First. ' + 'long ' * 400 + 'LAST! End?'
        self.assertTrue(sender(conn, text))
        self.assertEqual(' '.join(calls), text)

    def test_tts_clipping_endian_and_frame_lengths(self):
        sender, conn, _ = self.sender(np.array([-2., -1., -.5, 0., .5, 1., 2.]))
        self.assertTrue(sender(conn, 'test'))
        payloads = []
        for call in conn.sendall.call_args_list:
            frame = call.args[0]
            self.assertEqual(frame[0], 0x12)
            self.assertEqual(struct.unpack('>I', frame[1:5])[0], len(frame) - 5)
            self.assertEqual((len(frame) - 5) % 2, 0)
            self.assertLessEqual(len(frame) - 5, 4096)
            payloads.append(frame[5:])
        raw = b''.join(payloads)
        self.assertEqual(struct.unpack('<7h', raw[:14]), (-32767,-32767,-16383,0,16383,32767,32767))
        self.assertEqual(raw[14:], bytes(12000))

    def test_invalid_model_audio_never_starts_playback(self):
        for output in (np.array([]), np.zeros((2, 3)), np.array([np.nan]), np.array([np.inf])):
            sender, conn, _ = self.sender(output)
            self.assertFalse(sender(conn, 'test'))
            conn.begin_playback.assert_not_called()
            conn.sendall.assert_not_called()

    def test_model_failure_never_starts_playback(self):
        sender, conn, _ = self.sender(failure=True)
        self.assertFalse(sender(conn, 'test'))
        conn.begin_playback.assert_not_called()

    def process(self, raw):
        wav = MagicMock()
        stt = Mock(side_effect=RuntimeError('stop before external API'))
        state = dict(mode='PROCESSING', session_id='edge')
        remove = Mock()
        process = states.load_function('process_command', dict(
            np=np, log=Mock(), wave=NS(open=Mock(return_value=wav)), open=MagicMock(),
            os=NS(path=NS(exists=lambda _: True), remove=remove), time=NS(time=lambda: 0),
            groq_client=NS(audio=NS(transcriptions=NS(create=stt)))))
        return process, state, wav, remove, stt

    def test_agc_negative_full_scale_is_normalized_without_overflow(self):
        raw = struct.pack('<3h', -32768, 0, 32767)
        process, state, wav, _, _ = self.process(raw)
        process(None, raw, state)
        normalized = np.frombuffer(wav.__enter__.return_value.writeframes.call_args.args[0], dtype='<i2')
        self.assertEqual(normalized.tolist(), [-28000, 0, 27999])
        self.assertEqual(state['mode'], 'WAKE_WORD')

    def test_silence_agc_stays_zero(self):
        raw = bytes(960)
        process, _, wav, _, _ = self.process(raw)
        process(None, raw, {})
        self.assertEqual(wav.__enter__.return_value.writeframes.call_args.args[0], raw)

    def test_invalid_command_sizes_cleanup_and_restore_state(self):
        for raw in (b'', b'x', b'xyz'):
            process, state, wav, remove, stt = self.process(raw)
            with self.assertRaises(ValueError): process(None, raw, state)
            self.assertEqual(state['mode'], 'WAKE_WORD')
            remove.assert_called_once()
            wav.__enter__.assert_not_called()
            stt.assert_not_called()

    def test_actual_vad_peak_expression_handles_negative_full_scale(self):
        tree = ast.parse(states.SOURCE.read_text(encoding='utf-8-sig'))
        receiver = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'start_server')
        expr = next(n.value for n in ast.walk(receiver) if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'vol' for t in n.targets))
        for pcm, expected in (([-32768],32768), ([0],0), ([-32768,32767],32768)):
            peak = eval(compile(ast.Expression(expr), '<actual VAD peak>', 'eval'),
                        dict(np=np, pcm_np=np.array(pcm,dtype=np.int16)))
            self.assertEqual(peak, expected)

    def test_odd_microphone_payload_rejected_before_command(self):
        jobs, _, _ = states.StateTests().run_stream([b'x'])
        self.assertEqual(jobs, [])

if __name__ == '__main__': unittest.main()
