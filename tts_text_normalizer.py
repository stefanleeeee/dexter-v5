"""Conservative phonetic replacements on a TTS-only copy; standard library only."""
import re


TERMS = {
    'Python': 'Пайтон', 'GitHub': 'Гитхаб', 'Windows': 'Виндоус',
    'ChatGPT': 'Чат Джи-Пи-Ти', 'NVIDIA': 'Энвидиа', 'API': 'Эй-пи-ай',
    'Wi-Fi': 'Вай-фай', 'ESP32': 'И-Эс-Пэ тридцать два', 'USB': 'Ю-Эс-Би',
    'CPU': 'Си-Пи-Ю', 'GPU': 'Джи-Пи-Ю', 'RAM': 'Рэм', 'TCP': 'Ти-Си-Пи',
    'HTTP': 'Эйч-Ти-Ти-Пи', 'JSON': 'Джейсон',
}

# Preserve literal technical material. Unknown prose is retained, never guessed.
_PROTECTED = re.compile(
    r'```[\s\S]*?(?:```|\Z)|`[^`\n]*(?:`|$)'
    r'|\b(?:https?://|ftp://|www\.)[^\s<>]+'
    r'|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}'
    r'|(?<!\w)(?:[A-Za-z]:[\\/]|\\\\|~/|/)[^\s,;<>"\x27]+'
    r'|(?<![\w/])(?:[A-Za-z0-9_.-]+[\\/])+[A-Za-z0-9_.\\/-]*'
    r'|(?<!\w)[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+(?!\w)'
    r'|\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b'
    r'|(?<![\w-])(?:python(?:3(?:\.\d+)?)?\s+(?:-[A-Za-z]\b|[^\s]+\.py\b)'
    r'|pip3?\s+(?:install|uninstall|list|show|freeze)\b'
    r'|git\s+(?:clone|push|pull|status|diff|commit|checkout|switch|log)\b'
    r'|(?:curl|wget)\s+(?:-|https?://)'
    r'|(?:powershell|pwsh|cmd|bash|sh)\s+[-/]'
    r'|(?:npm|npx|yarn|node|cmake|idf\.py)\s+[^\n]+'
    r'|voice\s+(?:list|status|aidar|eugene|baya|kseniya|xenia)\b)[^\n]*',
    re.IGNORECASE,
)
_LATIN = re.compile(r'[A-Za-z]+')


def normalize_tts_text(text, enabled=True, terms=None):
    """Return adapted text, leaving protected strings and input unchanged.

    Extend TERMS or supply a complete custom mapping. This intentionally does not
    attempt to parse every programming language or infer unmarked commands.
    """
    if not enabled or not text:
        return text
    terms = TERMS if terms is None else terms
    if not terms:
        return text
    lookup = {word.casefold(): spoken for word, spoken in terms.items()}
    pattern = re.compile(r'(?<![\w-])(?:' + '|'.join(
        re.escape(word) for word in sorted(terms, key=len, reverse=True)) + r')(?![\w-])', re.IGNORECASE)
    def adapt(part):
        return pattern.sub(lambda match: lookup[match.group().casefold()], part)
    result, position = [], 0
    for match in _PROTECTED.finditer(text):
        result.extend((adapt(text[position:match.start()]), match.group()))
        position = match.end()
    result.append(adapt(text[position:]))
    return ''.join(result)


def remaining_latin_count(text):
    """Counts only: diagnostics must never log the terms or protected contents."""
    return len(_LATIN.findall(text))
