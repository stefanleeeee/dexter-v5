"""Bounded synchronous Groq delta reader. No model or socket-writer ownership."""
import threading
import time
from llm_options import completion_options, usage_numbers
from session_runtime import CommandCancelled


class StreamError(RuntimeError):
    pass


class GroqTextStream:
    MAX_CHARS = 32768

    def __init__(self, create, check_active, *, messages, clock=time.perf_counter, options=None):
        self.create = create
        self.check_active = check_active
        self.messages = messages
        self.options = completion_options(streaming=True) if options is None else dict(options)
        if 'reasoning_format' in self.options and 'include_reasoning' in self.options:
            raise ValueError('Do not mix reasoning_format and include_reasoning')
        self.usage = usage_numbers(None)
        self.clock = clock
        self.started = clock()
        self.first_text_s = None
        self.completed_s = None
        self.finish_reason = None
        self.cancel_source = None
        self.parts = []
        self._size = 0
        self._stream = None
        self._stop = threading.Event()
        self._close_lock = threading.Lock()
        self._closed = False
        self._watcher = None

    @property
    def text(self):
        return ''.join(self.parts)

    def __enter__(self):
        self.check_active()
        self._stream = self.create(messages=self.messages, **self.options)
        self._watcher = threading.Thread(target=self._watch_cancel,
                                         name='groq-stream-close', daemon=True)
        self._watcher.start()
        try:
            self.check_active()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def _watch_cancel(self):
        while not self._stop.wait(0.05):
            try:
                self.check_active()
            except Exception as error:
                self.cancel_source = 'user_cancel' if isinstance(error, CommandCancelled) else 'session_inactive'
                self._close()
                return

    def _close(self):
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            stream = self._stream
        # Installed SDK.close closes its HTTP response, not an executing generator.
        # No locks are held during IO. Cross-thread close remains best effort.
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass

    def __iter__(self):
        if self._stream is None:
            raise RuntimeError('Use GroqTextStream as a context manager')
        iterator = iter(self._stream)
        while True:
            self.check_active()  # Do not initiate another HTTP read after known CANCEL.
            try:
                chunk = next(iterator)
            except StopIteration:
                break
            self.check_active()
            # Usage-only final chunks can have no choices. Never inspect/log reasoning.
            usage = getattr(chunk, 'usage', None)
            x_groq = getattr(chunk, 'x_groq', None)
            if usage is None:
                usage = x_groq.get('usage') if isinstance(x_groq, dict) else getattr(x_groq, 'usage', None)
            numbers = usage_numbers(usage)
            self.usage.update({key:value for key,value in numbers.items() if value is not None})
            choices = getattr(chunk, 'choices', None) or []
            if not choices:
                continue
            choice = choices[0]
            if getattr(choice, 'index', 0) not in (None, 0):
                raise StreamError('Unexpected completion choice index')
            delta = getattr(choice, 'delta', None)
            content = getattr(delta, 'content', None)
            if content:
                if not isinstance(content, str):
                    raise StreamError('Invalid text delta')
                if self.finish_reason is not None:
                    raise StreamError('Text after completion finish')
                if self._size + len(content) > self.MAX_CHARS:
                    raise StreamError('Response exceeds bounded text budget')
                self.parts.append(content)
                self._size += len(content)
                if self.first_text_s is None:
                    self.first_text_s = self.clock() - self.started
                yield content
            reason = getattr(choice, 'finish_reason', None)
            if reason is not None:
                if self.finish_reason is not None and self.finish_reason != reason:
                    raise StreamError('Conflicting finish reasons')
                self.finish_reason = reason
        self.check_active()
        self.completed_s = self.clock() - self.started
        if self.finish_reason not in {'stop', 'length'}:
            # Length is a bounded partial result; all other abnormal finishes fail.
            raise StreamError('Incomplete text stream: ' + str(self.finish_reason))

    def __exit__(self, *args):
        self._stop.set()
        self._close()
        if self._watcher is not None:
            self._watcher.join(0.2)
