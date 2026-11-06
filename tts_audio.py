"""Shared CPU-TTS output validation/PCM conversion; no transport or model loads."""
import numpy as np


MAX_PHRASE_SAMPLES = 24000 * 100  # 100 s sanity cap per bounded text phrase


def synthesize_samples(model, text, speaker, check_active):
    check_active()
    tensor = model.apply_tts(text=text, speaker=speaker, sample_rate=24000)
    check_active()  # A completed uninterruptible model call can already be stale.
    samples = np.asarray(tensor.numpy())
    if (samples.ndim != 1 or samples.size == 0 or
            samples.size > MAX_PHRASE_SAMPLES or not np.isfinite(samples).all()):
        raise ValueError('Invalid TTS audio: expected bounded nonempty finite mono samples')
    return samples


def encode_pcm(samples):
    return (np.clip(samples, -1.0, 1.0) * 32767).astype('<i2').tobytes()


def bounded_tts_parts(text, limit=900):
    while len(text) > limit:
        boundary = max(text.rfind(' ', 0, limit + 1), text.rfind('\n', 0, limit + 1))
        if boundary <= 0:
            raise ValueError('Prepared TTS word exceeds bounded phrase length')
        if text[:boundary].strip():
            yield text[:boundary]
        text = text[boundary:].lstrip()
    if text.strip():
        yield text
