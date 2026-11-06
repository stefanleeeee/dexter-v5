"""3.1: delta fidelity and cooperative close; no API/network."""
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
from llm_stream import GroqTextStream, StreamError
from session_runtime import CommandCancelled


def chunk(text=None, reason=None):
    return NS(choices=[NS(index=0, delta=NS(content=text), finish_reason=reason)])


class FakeStream:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
    def __iter__(self):
        yield from self.chunks
    def close(self):
        self.closed = True


class LLMStreamTests(unittest.TestCase):
    def collect(self, chunks):
        source = FakeStream(chunks)
        create = Mock(return_value=source)
        with GroqTextStream(create, lambda: None, messages=[]) as reader:
            parts = list(reader)
        self.assertTrue(source.closed)
        self.assertTrue(create.call_args.kwargs['stream'])
        self.assertEqual(create.call_args.kwargs['max_completion_tokens'], 2048)
        self.assertNotIn('max_tokens',create.call_args.kwargs)
        return reader, parts

    def test_split_word_order_and_empty_deltas(self):
        reader, parts = self.collect([chunk(), NS(choices=[]), chunk('Пайт'), chunk('он '),
                                      chunk('и Git'), chunk('Hub.'), chunk(reason='stop')])
        self.assertEqual(''.join(parts), 'Пайтон и GitHub.')
        self.assertEqual(reader.text, ''.join(parts))
        self.assertEqual(reader.finish_reason, 'stop')

    def test_finish_on_same_chunk_preserves_content(self):
        reader, parts = self.collect([chunk('Привет!', 'stop')])
        self.assertEqual(reader.text, 'Привет!')

    def test_empty_normal_response(self):
        reader, parts = self.collect([chunk(reason='stop')])
        self.assertEqual(parts, [])
        self.assertIsNone(reader.first_text_s)

    def test_missing_and_abnormal_finish_preserve_raw_but_fail(self):
        for reason in [None, 'content_filter', 'tool_calls']:
            source = FakeStream([chunk('неполный'), chunk(reason=reason)])
            with GroqTextStream(Mock(return_value=source), lambda: None, messages=[]) as reader:
                with self.assertRaises(StreamError): list(reader)
            self.assertEqual(reader.text, 'неполный')
            self.assertTrue(source.closed)

    def test_content_after_finish_is_rejected(self):
        with self.assertRaises(StreamError):
            self.collect([chunk('One.', 'stop'), chunk('Extra')])

    def test_text_budget_is_bounded(self):
        with self.assertRaises(StreamError):
            self.collect([chunk('x' * (GroqTextStream.MAX_CHARS + 1))])

    def test_api_error_closes_source(self):
        def broken():
            yield chunk('First.')
            raise RuntimeError('synthetic API error')
        source = FakeStream(broken())
        with GroqTextStream(Mock(return_value=source), lambda: None, messages=[]) as reader:
            with self.assertRaises(RuntimeError): list(reader)
        self.assertTrue(source.closed)

    def test_cancel_before_request(self):
        create = Mock()
        with self.assertRaises(CommandCancelled):
            with GroqTextStream(create, Mock(side_effect=CommandCancelled()), messages=[]): pass
        create.assert_not_called()

    def test_cancel_during_read_closes_and_discards_late_delta(self):
        cancelled, entered, closed = threading.Event(), threading.Event(), threading.Event()
        class Blocking:
            def __iter__(self):
                entered.set()
                if not closed.wait(2): raise RuntimeError('watcher failed')
                yield chunk('STALE')
            def close(self): closed.set()
        def check():
            if cancelled.is_set(): raise CommandCancelled()
        def cancel():
            entered.wait(2); cancelled.set()
        thread = threading.Thread(target=cancel); thread.start()
        try:
            with GroqTextStream(Mock(return_value=Blocking()), check, messages=[]) as reader:
                with self.assertRaises(CommandCancelled): list(reader)
            self.assertEqual(reader.text, '')
            self.assertTrue(closed.is_set())
        finally: thread.join(2)


if __name__ == '__main__': unittest.main()
