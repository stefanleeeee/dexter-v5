"""Local console signal only; never writes the ESP socket or invokes models."""
import sys
import threading


class CancelControl:
    def __init__(self, log, stream=None, voices=None, memory=None):
        self.memory = memory
        self.voices = voices
        self.log = log
        self.stream = stream if stream is not None else sys.stdin
        self._lock = threading.Lock()
        self._session = None

    def attach(self, session):
        with self._lock:
            self._session = session

    def cancel_current(self):
        with self._lock:
            session = self._session
        command_id = session.command_id if session is not None else None
        if command_id is not None and session.request_cancel(expected_id=command_id):
            return True
        return bool(session is not None and session.cancel_followup())

    def run(self):
        try:
            for line in self.stream:
                if line.strip().casefold() in ('cancel', 'отмена'):
                    if not self.cancel_current(): self.log('[CANCEL] No active command')
                elif self.memory is not None and line.strip().casefold().split(maxsplit=1)[0:1] == ['memory']:
                    parts = line.strip().split(maxsplit=1)
                    argument = parts[1] if len(parts) == 2 else ''
                    if argument.strip().casefold() == 'clear':
                        with self._lock:
                            session = self._session
                        if session is not None: session.cancel_followup(invalidate=True)
                    self.memory.command(argument, self.log)
                elif self.voices is not None and line.strip().casefold().split(maxsplit=1)[0:1] == ['voice']:
                    parts = line.strip().split(maxsplit=1)
                    self.voices.command(parts[1] if len(parts) == 2 else '', self.log)
        except (OSError, ValueError):
            self.log('[CANCEL] Console input unavailable; programmatic control remains available')

    def start(self):
        threading.Thread(target=self.run, name='dexter-cancel-console', daemon=True).start()
        self.log('[CONTROL] Type cancel or отмена + Enter to cancel the current response')
        if self.memory is not None: self.log('[CONTROL] memory status | memory clear')
        if self.voices is not None: self.log('[CONTROL] voice list | voice status | voice <name>')
