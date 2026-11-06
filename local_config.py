"""Small local configuration reader. No models, network or third-party imports."""
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, repr=False)
class Config:
    api_key: str
    host: str
    port: int
    tts_model: str = 'v5_6_ru'
    tts_voice: str = 'aidar'
    tts_normalize_en: bool = True
    llm_streaming: bool = True
    llm_max_completion_tokens: int = 2048
    llm_reasoning_effort: str = 'low'
    llm_include_reasoning: bool = False
    dialog_memory_enabled: bool = True
    dialog_memory_max_turns: int = 6
    dialog_memory_max_tokens: int = 1500
    dialog_followup_enabled: bool = True
    dialog_followup_timeout_s: float = 12.0


def load_config(path=None, environ=None):
    env = os.environ if environ is None else environ
    path = Path(__file__).with_name('.env') if path is None else Path(path)
    values = {}
    try:
        if path.exists():
            for line in path.read_text(encoding='utf-8-sig').splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                key, separator, value = line.partition('=')
                key, value = key.strip(), value.strip()
                if not separator or key not in {'GROQ_API_KEY', 'HOST', 'PORT', 'TTS_MODEL', 'TTS_VOICE', 'TTS_NORMALIZE_EN', 'LLM_STREAMING', 'LLM_MAX_COMPLETION_TOKENS', 'LLM_REASONING_EFFORT', 'LLM_INCLUDE_REASONING', 'DIALOG_MEMORY_ENABLED', 'DIALOG_MEMORY_MAX_TURNS', 'DIALOG_MEMORY_MAX_TOKENS', 'DIALOG_FOLLOWUP_ENABLED', 'DIALOG_FOLLOWUP_TIMEOUT_S'}:
                    raise ValueError('Invalid .env entry; use the supplied .env.example')
                if value.startswith('"'):
                    value = json.loads(value)
                elif value.startswith("'"):
                    if not value.endswith("'") or len(value) < 2:
                        raise ValueError('Invalid .env quoting')
                    value = value[1:-1]
                if not isinstance(value, str):
                    raise ValueError('Invalid .env value')
                values[key] = value
    except (OSError, UnicodeError, ValueError):
        raise ValueError('Cannot read local .env; check format and permissions') from None
    values.update({key: env[key] for key in ('GROQ_API_KEY', 'HOST', 'PORT', 'TTS_MODEL', 'TTS_VOICE', 'TTS_NORMALIZE_EN', 'LLM_STREAMING', 'LLM_MAX_COMPLETION_TOKENS', 'LLM_REASONING_EFFORT', 'LLM_INCLUDE_REASONING', 'DIALOG_MEMORY_ENABLED', 'DIALOG_MEMORY_MAX_TURNS', 'DIALOG_MEMORY_MAX_TOKENS', 'DIALOG_FOLLOWUP_ENABLED', 'DIALOG_FOLLOWUP_TIMEOUT_S') if key in env})
    api_key = values.get('GROQ_API_KEY', '').strip()
    if not api_key or api_key == 'YOUR_GROQ_API_KEY':
        raise ValueError('Set GROQ_API_KEY in the environment or local .env')
    host = values.get('HOST', '0.0.0.0').strip()
    if not host or any(c.isspace() for c in host):
        raise ValueError('HOST must be a nonempty bind address')
    try:
        port = int(values.get('PORT', '3333'))
        if not 1 <= port <= 65535:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('PORT must be an integer between 1 and 65535') from None
    model = values.get('TTS_MODEL', 'v5_6_ru').strip()
    if model not in {'v4_ru', 'v5_6_ru'}:
        raise ValueError('TTS_MODEL must be v4_ru or v5_6_ru')
    voice = values.get('TTS_VOICE', 'aidar').strip().casefold()
    if not re.fullmatch(r'[a-z][a-z0-9_]*', voice):
        raise ValueError('Invalid TTS_VOICE; use voice list to see available speakers')
    normalize = values.get('TTS_NORMALIZE_EN', 'true').strip().casefold()
    if normalize not in {'true', 'false', '1', '0'}:
        raise ValueError('TTS_NORMALIZE_EN must be true/false or 1/0')
    streaming = values.get('LLM_STREAMING', 'true').strip().casefold()
    if streaming not in {'true', 'false', '1', '0'}:
        raise ValueError('LLM_STREAMING must be true/false or 1/0')
    try:
        tokens = int(values.get('LLM_MAX_COMPLETION_TOKENS', '2048'))
        if not 1 <= tokens <= 32768: raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('LLM_MAX_COMPLETION_TOKENS must be an integer between 1 and 32768') from None
    effort = values.get('LLM_REASONING_EFFORT', 'low').strip().casefold()
    if effort not in {'low', 'medium', 'high'}:
        raise ValueError('LLM_REASONING_EFFORT must be low, medium or high for GPT-OSS')
    include = values.get('LLM_INCLUDE_REASONING', 'false').strip().casefold()
    if include not in {'true', 'false', '1', '0'}:
        raise ValueError('LLM_INCLUDE_REASONING must be true/false or 1/0')
    memory = values.get('DIALOG_MEMORY_ENABLED', 'true').strip().casefold()
    if memory not in {'true', 'false', '1', '0'}:
        raise ValueError('DIALOG_MEMORY_ENABLED must be true/false or 1/0')
    def memory_limit(key, default, upper):
        try:
            value = int(values.get(key, str(default)))
            if not 1 <= value <= upper: raise ValueError()
            return value
        except (ValueError, TypeError):
            raise ValueError(f'{key} must be an integer between 1 and {upper}') from None
    turns = memory_limit('DIALOG_MEMORY_MAX_TURNS', 6, 100)
    memory_tokens = memory_limit('DIALOG_MEMORY_MAX_TOKENS', 1500, 32768)
    followup = values.get('DIALOG_FOLLOWUP_ENABLED', 'true').strip().casefold()
    if followup not in {'true', 'false', '1', '0'}:
        raise ValueError('DIALOG_FOLLOWUP_ENABLED must be true/false or 1/0')
    try:
        followup_timeout = float(values.get('DIALOG_FOLLOWUP_TIMEOUT_S', '12'))
        if not math.isfinite(followup_timeout) or not 1 <= followup_timeout <= 60:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('DIALOG_FOLLOWUP_TIMEOUT_S must be a finite number between 1 and 60') from None
    return Config(api_key, host, port, model, voice, normalize in {'true', '1'}, streaming in {'true', '1'}, tokens, effort, include in {'true', '1'}, memory in {'true', '1'}, turns, memory_tokens, followup in {'true', '1'}, followup_timeout)


def redact_log(message, config):
    text = str(message).replace(config.api_key, '[REDACTED]')
    text = re.sub(r'\bgsk_[A-Za-z0-9_-]+', '[REDACTED]', text)
    return re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', '[IP]', text)


def followup_status(config):
    """Startup status only; support/readiness must be confirmed per TCP session."""
    return dict(requested=config.dialog_followup_enabled, supported=False, active=False,
                timeout_s=config.dialog_followup_timeout_s,
                reason='awaiting_firmware_capability' if config.dialog_followup_enabled else 'disabled')
