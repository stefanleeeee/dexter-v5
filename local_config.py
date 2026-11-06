"""Small local configuration reader. No models, network or third-party imports."""
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, repr=False)
class Config:
    api_key: str
    host: str
    port: int


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
                if not separator or key not in {'GROQ_API_KEY', 'HOST', 'PORT'}:
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
    values.update({key: env[key] for key in ('GROQ_API_KEY', 'HOST', 'PORT') if key in env})
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
    return Config(api_key, host, port)


def redact_log(message, config):
    text = str(message).replace(config.api_key, '[REDACTED]')
    text = re.sub(r'\bgsk_[A-Za-z0-9_-]+', '[REDACTED]', text)
    return re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', '[IP]', text)
