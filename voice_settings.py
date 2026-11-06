"""Thread-safe speaker selection; never loads models or touches a socket."""
import threading


class VoiceSettings:
    def __init__(self, model_name, speakers, initial='aidar'):
        self.model_name = model_name
        self.speakers = tuple(s for s in speakers if s != 'random')
        if initial not in self.speakers:
            raise ValueError('TTS_VOICE is not available in the selected model')
        self._speaker = initial
        self._lock = threading.Lock()

    def snapshot(self):
        with self._lock:
            return self._speaker

    def command(self, argument, log):
        argument = argument.strip().casefold()
        if argument == 'list':
            log('[VOICE] Available: ' + ', '.join(self.speakers))
        elif argument == 'status':
            log(f'[VOICE] Model={self.model_name}, speaker={self.snapshot()}, CPU, 24000 Hz')
        elif argument in self.speakers:
            with self._lock:
                self._speaker = argument
            log(f'[VOICE] Selected {argument}; applies to the next TTS response, not audio already in progress')
        else:
            log('[VOICE] Unknown voice/command. Use voice list or voice status')
