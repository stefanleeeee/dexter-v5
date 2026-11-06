"""Connection/worker ownership only; no model or protocol dependencies."""
import socket
import os
import struct
import threading
import uuid


# Includes history, cloud calls and Silero: a disconnected worker may still be
# inside an uninterruptible SDK/model call when the next client connects.
_PROCESS_LOCK = threading.Lock()
PLAYBACK_TOKEN_BYTES = 32
PLAYBACK_ACK_TIMEOUT = 10.0
CANCEL_TIMEOUT = 2.0


class CommandCancelled(Exception):
    """Cooperative cancellation; distinct from connection loss and success."""


class CommandSession:
    def __init__(self, conn, log):
        self.conn = conn
        self.log = log
        self.generation = uuid.uuid4().hex
        self.state = {'mode': 'WAKE_WORD'}  # Only the receiver thread writes it.
        self._gate = threading.Lock()
        self._cancelled = threading.Event()
        self._done = threading.Event()
        self._worker = None
        self._writer = None
        self._playback_token = None
        self._playback_eos = False
        self._playback_ack = threading.Event()
        self._playback_sequence = 0
        self._command_cancelled = threading.Event()
        self._cancel_ack = threading.Event()
        self._cancel_on_wire = False
        self._cancel_cleanup_done = False
        self._cancel_timer = None
        self._command_id = None
        self._command_finished = False
        self._playback_finished = False
        self._playback_started = False

    @property
    def command_id(self):
        with self._gate:
            return self._command_id if not self._command_finished and not self._cancelled.is_set() else None

    def request_cancel(self, expected_id=None):
        timer = None
        with self._gate:
            if (self._cancelled.is_set() or self._worker is None or self._command_finished or
                    self._playback_finished or (expected_id is not None and
                    expected_id not in (self._command_id, self._playback_token))):
                return False
            if self._command_cancelled.is_set():
                return True
            self._command_cancelled.set()
            self._playback_ack.set()  # Wake ACK wait, never turn it into success.
            if self._playback_token is not None:
                timer = threading.Timer(CANCEL_TIMEOUT, self._abort_stuck_cancel, args=(self._command_id,))
                timer.daemon = True
                self._cancel_timer = timer
        self.log('[CANCEL REQUESTED] Local request accepted')
        if timer is not None: timer.start()
        return True

    def _abort_stuck_cancel(self, command_id):
        with self._gate:
            if (self._cancelled.is_set() or self._command_id != command_id or
                    not self._command_cancelled.is_set() or self._cancel_cleanup_done):
                return
            self._cancelled.set()
            self._playback_ack.set()
            self._cancel_ack.set()
        self.log('[CANCEL FALLBACK] No cancellation confirmation; aborting TCP')
        self._close_socket(abort=True)

    def receive_cancel_ack(self, token):
        with self._gate:
            if (not self._cancelled.is_set() and self._command_cancelled.is_set() and
                    self._cancel_on_wire and self._playback_token == token and len(token) == 32):
                self._cancel_ack.set()
                return True
        return False

    def _complete_cancel(self):
        with self._gate:
            if self._cancelled.is_set():
                self.log('[CANCEL DISCONNECTED] No success confirmation')
                return
            token = self._playback_token
            started = self._playback_started
            self._cancel_on_wire = token is not None and started
            if not started:
                self._playback_token = None
                self._playback_eos = False
        if token is None or not started:
            self.log('[COMMAND CANCELLED] No audio was started')
            return
        self._send_raw(struct.pack('>BI', 0x15, 32) + token)
        if not self._cancel_ack.wait(CANCEL_TIMEOUT):
            self.close(abort=True)
            raise TimeoutError('No CANCELLED ACK')
        self._check_connection()
        with self._gate:
            self._playback_token = None
            self._playback_eos = False
            self._playback_started = False
            self._cancel_cleanup_done = True
        self.log(f'[PLAYBACK CANCELLED] {token[-4:].hex()}: ESP output silenced')

    def begin_playback(self):
        if threading.get_ident() != self._writer:
            raise RuntimeError('Only the command writer may begin playback')
        with self._gate:
            self.check_active()
            if self._playback_token is not None:
                raise RuntimeError('Playback already pending')
            self._playback_sequence += 1
            token = bytes.fromhex(self.generation) + self._playback_sequence.to_bytes(16, 'big')
            self._playback_token = token
            self._playback_eos = False
            self._playback_ack = threading.Event()
        self.check_active()
        self._send_raw(struct.pack('>BI', 0x11, len(token)) + token)
        with self._gate:
            self._playback_started = True
        self.check_active()
        return token

    def receive_playback_ack(self, token):
        # Called only by the receiver of this socket, never by the writer.
        with self._gate:
            if (not self._cancelled.is_set() and not self._command_cancelled.is_set() and self._playback_eos and
                    self._playback_token == token and len(token) == PLAYBACK_TOKEN_BYTES):
                self._playback_ack.set()
                return True
        return False

    def finish_playback(self, token):
        if threading.get_ident() != self._writer:
            raise RuntimeError('Only the command writer may finish playback')
        with self._gate:
            self.check_active()
            if token != self._playback_token:
                raise RuntimeError('Playback token does not match')
            self._playback_eos = True
            event = self._playback_ack
        self.sendall(struct.pack('>BI', 0x13, len(token)) + token)
        if not event.wait(PLAYBACK_ACK_TIMEOUT):
            self.close()
            raise TimeoutError('No playback ACK; update ESP firmware or check I2S/connection')
        self.check_active()  # Disconnect wakes the event but is never success.
        with self._gate:
            self.check_active()
            self._playback_token = None
            self._playback_eos = False
            self._playback_finished = True
        self.log(f'[PLAYBACK DONE] {token[-4:].hex()}: ESP I2S drain acknowledged')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def check_active(self):
        self._check_connection()
        if self._command_cancelled.is_set():
            raise CommandCancelled('Current command was cancelled')

    def _check_connection(self):
        if self._cancelled.is_set():
            raise ConnectionAbortedError('Connection generation is no longer active')

    def commit(self, action):
        # Disconnect and history commit have an explicit ordering.
        with self._gate:
            self.check_active()
            return action()

    def sendall(self, data):
        self.check_active()
        self._send_raw(data)
        self.check_active()

    def _send_raw(self, data):
        self._check_connection()
        if threading.get_ident() != self._writer:
            raise RuntimeError('Only the active command worker may send audio')
        try:
            self.conn.sendall(data)
        except OSError:
            # A partial sendall may have left an incomplete frame on the wire.
            # Never reuse that connection for another response.
            self.close()
            raise

    def start(self, process, audio, thread_factory=threading.Thread):
        self._check_connection()
        if self._worker is not None:
            raise RuntimeError('A command is already active on this connection')
        worker_state = dict(self.state)
        worker_state['session_id'] = self.generation + '_' + uuid.uuid4().hex
        with self._gate:
            self._command_id = worker_state['session_id']
            self._command_finished = self._playback_finished = False
            self._playback_started = False
            self._command_cancelled = threading.Event()
            self._cancel_ack = threading.Event()
            self._cancel_on_wire = self._cancel_cleanup_done = False
        self._done.clear()
        worker = thread_factory(target=self.run, args=(process, audio, worker_state), daemon=True)
        self._worker = worker
        try:
            worker.start()
        except Exception:
            self._worker = None
            self._command_finished = True
            raise

    def run(self, process, audio, worker_state):
        acquired = False
        try:
            while not acquired:
                self.check_active()
                acquired = _PROCESS_LOCK.acquire(timeout=0.05)
            self.check_active()
            self._writer = threading.get_ident()
            process(self, audio, worker_state, session=self)
        except CommandCancelled:
            self.log('[CANCEL] Worker stopped at a safe boundary')
        except Exception as error:
            self.log(f'[{self.generation}] Worker stopped: {error}')
        finally:
            try:
                if self._command_cancelled.is_set(): self._complete_cancel()
            except Exception as error:
                self.log(f'[CANCEL ERROR] {error}')
                self.close(abort=True)
            with self._gate:
                self._command_finished = self._cancel_cleanup_done = True
                timer = self._cancel_timer
                self._cancel_timer = None
            if timer is not None: timer.cancel()
            self._writer = None
            if acquired:
                _PROCESS_LOCK.release()
            self._done.set()

    def poll(self):
        # Called by the receiver, not by the worker: it owns the mode.
        if self._done.is_set():
            self._worker = None
            self._done.clear()
            if not self._cancelled.is_set():
                self.state['mode'] = 'WAKE_WORD'
                self.log('[ОЖИДАНИЕ] Декстер снова ждет своего имени...')

    def close(self, abort=False):
        with self._gate:
            if self._cancelled.is_set():
                return
            self._cancelled.set()
            self._playback_ack.set()
            self._cancel_ack.set()
        self._close_socket(abort)

    def _close_socket(self, abort=False):
        if abort:
            try:
                self.conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                     struct.pack('HH' if os.name == 'nt' else 'ii', 1, 0))
            except OSError:
                pass
        # Do not wait for an SDK call or a blocking send. Shutdown unblocks the
        # socket reader/writer; the old worker retains only this socket object.
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.conn.close()
