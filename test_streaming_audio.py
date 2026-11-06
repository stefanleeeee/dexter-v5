import struct
import threading
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import numpy as np

from tts_audio import synthesize_samples, encode_pcm, bounded_tts_parts
from streaming_reply import stream_reply
from test_llm_stream import FakeStream, chunk
from session_runtime import CommandCancelled


class StreamingAudioTests(unittest.TestCase):
    def run_reply(self, chunks, model=None, conn=None):
        conn = conn or Mock()
        conn.begin_playback.return_value = b'x' * 32
        model = model or NS(apply_tts=Mock(return_value=NS(numpy=lambda:np.array([.1, -.1]))))
        source = FakeStream(chunks)
        result = stream_reply(conn, Mock(return_value=source), [], model, 'eugene', True, Mock())
        return conn, model, result, source

    def test_finite_mono_clipped_little_endian(self):
        self.assertEqual(struct.unpack('<3h', encode_pcm(np.array([-2.,0.,2.]))),(-32767,0,32767))
        for samples in [np.array([]),np.array([np.nan]),np.array([np.inf]),np.zeros((2,2))]:
            with self.assertRaises(ValueError):
                synthesize_samples(NS(apply_tts=lambda **kw:NS(numpy=lambda:samples)), 'test','aidar',lambda:None)

    def test_explicit_audio_budget(self):
        with patch('tts_audio.MAX_PHRASE_SAMPLES', 2):
            with self.assertRaises(ValueError):
                synthesize_samples(NS(apply_tts=lambda **kw:NS(numpy=lambda:np.zeros(3))),'t','aidar',lambda:None)

    def test_normalizer_expansion_splits_safely(self):
        text = 'Джи-Пи-Ю ' * 300
        parts=list(bounded_tts_parts(text))
        self.assertTrue(all(len(p)<=900 for p in parts))
        self.assertEqual(' '.join(p.strip() for p in parts),text.strip())
        with self.assertRaises(ValueError):list(bounded_tts_parts('x'*901))

    def test_one_speaker_and_first_audio_before_remaining_llm(self):
        conn=Mock();calls=[]
        def deltas():
            yield chunk('Python работает. ')
            yield chunk('GitHub ')
            self.assertTrue(conn.sendall.called)
            yield chunk('помогает!')
            yield chunk(reason='stop')
        model=NS(apply_tts=Mock(side_effect=lambda **kw:(calls.append(kw) or NS(numpy=lambda:np.array([.1])))))
        conn,_,(text,metrics),source=self.run_reply(deltas(),model,conn)
        self.assertEqual(text,'Python работает. GitHub помогает!')
        self.assertEqual([c['speaker'] for c in calls],['eugene','eugene'])
        self.assertEqual([c['text'].strip() for c in calls],['Пайтон работает.','Гитхаб помогает!'])
        self.assertEqual(metrics['queued_phrases'],0)
        self.assertEqual(metrics['max_inflight_phrases'],1)
        self.assertLessEqual(metrics['text_buffer_high'],900)
        self.assertLess(metrics['first_send_s'],metrics['llm_complete_s'])

    def test_empty_stream_does_not_start_audio(self):
        conn,model,(text,metrics),source=self.run_reply([chunk(reason='stop')])
        self.assertEqual(text,'')
        model.apply_tts.assert_not_called();conn.begin_playback.assert_not_called()
        conn.finish_playback.assert_not_called()

    def test_no_pause_per_sentence_and_one_final_tail(self):
        conn,model,_,_=self.run_reply([chunk('Первое. Второе. Третье.'),chunk(reason='stop')])
        pcm=b''.join(c.args[0][5:] for c in conn.sendall.call_args_list)
        self.assertEqual(pcm,encode_pcm(np.array([.1,-.1]))*3+bytes(12000))
        conn.begin_playback.assert_called_once();conn.finish_playback.assert_called_once()

    def test_blocked_send_backpressures_model_and_llm_and_cancel_remains_visible(self):
        entered,release,cancelled=threading.Event(),threading.Event(),threading.Event()
        reads=[];errors=[];model=NS(apply_tts=Mock(return_value=NS(numpy=lambda:np.array([.1]))))
        conn=Mock();conn.begin_playback.return_value=b'x'*32
        def check():
            if cancelled.is_set():raise CommandCancelled()
        conn.check_active.side_effect=check
        def send(data):
            entered.set()
            if not release.wait(2):raise RuntimeError('blocked test send')
        conn.sendall.side_effect=send
        def deltas():
            for text in ['Первая фраза. ','Вторая ','СТАРЫЕ ДАННЫЕ']:
                reads.append(text);yield chunk(text)
            yield chunk(reason='stop')
        source=FakeStream(deltas())
        def worker():
            try:stream_reply(conn,Mock(return_value=source),[],model,'aidar',True,Mock())
            except BaseException as e:errors.append(type(e))
        thread=threading.Thread(target=worker);thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(model.apply_tts.call_count,1)
            self.assertEqual(len(reads),2)
            cancelled.set();release.set();thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors,[CommandCancelled])
            self.assertEqual(model.apply_tts.call_count,1)
            self.assertEqual(len(reads),2)
            conn.finish_playback.assert_not_called()
            self.assertTrue(source.closed)
        finally:release.set();thread.join(2)

    def test_cancel_during_model_call_discards_output(self):
        active=Mock();conn=Mock(check_active=active)
        def synth(**kw):
            active.side_effect=CommandCancelled()
            return NS(numpy=lambda:np.array([.1]))
        with self.assertRaises(CommandCancelled):
            self.run_reply([chunk('Первая фраза. Потом')],NS(apply_tts=synth),conn)
        conn.sendall.assert_not_called();conn.begin_playback.assert_not_called()


if __name__=='__main__':unittest.main()
