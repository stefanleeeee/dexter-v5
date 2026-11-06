"""Local console signal only; never writes the ESP socket or invokes models."""
import sys
import threading


class CancelControl:
    def __init__(self, log, stream=None):
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
        return bool(command_id is not None and session.request_cancel(expected_id=command_id))

    def run(self):
        try:
            for line in self.stream:
                if line.strip().casefold() in ('cancel', 'отмена'):
                    if not self.cancel_current(): self.log('[CANCEL] No active command')
        except (OSError, ValueError):
            self.log('[CANCEL] Console input unavailable; programmatic control remains available')

    def start(self):
        threading.Thread(target=self.run, name='dexter-cancel-console', daemon=True).start()
        self.log('[CONTROL] Type cancel or отмена + Enter to cancel the current response')
