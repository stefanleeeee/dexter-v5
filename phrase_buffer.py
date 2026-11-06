"""Incremental, bounded phrase segmentation; original deltas stay separate."""
import re


class PhraseLimitError(ValueError):
    pass


class PhraseBuffer:
    ABBREVIATIONS = {'г', 'ул', 'им', 'др', 'рис', 'см', 'стр', 'руб', 'коп',
                     'т', 'е', 'д', 'п', 'e', 'g', 'i', 'mr', 'mrs', 'dr', 'etc'}

    def __init__(self, max_chars=900, soft_chars=400):
        self.max_chars = max_chars
        self.soft_chars = min(soft_chars, max_chars)
        self.pending = ''
        self.high_water = 0

    def _boundary(self):
        text = self.pending
        for match in re.finditer(r'[.!?…]+[»"\x27)\]]*', text):
            end = match.end()
            if end == len(text):
                continue  # Need lookahead across delta boundaries.
            next_char = text[end]
            punct = match.group().rstrip('»"\x27)]')
            if not next_char.isspace() and not (punct[-1] in '!?…' and next_char.isupper()):
                continue  # decimal, dotted identifier/URL or incomplete punctuation.
            if punct == '.':
                before = text[:match.start()]
                word = re.search(r'([\w]+)$', before)
                word = word.group(1) if word else ''
                if word.casefold() in self.ABBREVIATIONS:
                    continue
                if len(word) == 1 and word.isalpha() and word.isupper() and word != 'Я':
                    continue  # name initial / dotted acronym
            # Wait for a non-whitespace lookahead; retain all separators verbatim.
            while end < len(text) and text[end].isspace():
                end += 1
            if end < len(text):
                return end
        return None

    def feed(self, delta):
        for char in delta:
            self.pending += char
            self.high_water = max(self.high_water, len(self.pending))
            boundary = self._boundary()
            if boundary is None and len(self.pending) >= self.soft_chars:
                candidates = [m.end() for m in re.finditer(r'[,;]\s+', self.pending)
                              if m.end() >= 80 and m.end() < len(self.pending)]
                if candidates:
                    boundary = candidates[-1]
            if boundary is None and len(self.pending) >= self.max_chars:
                spaces = [m.end() for m in re.finditer(r'\s+', self.pending)
                          if m.end() >= 80 and m.end() < len(self.pending)]
                if not spaces:
                    raise PhraseLimitError('Unbroken text exceeds phrase budget; word not split')
                boundary = spaces[-1]
            if boundary is not None:
                phrase, self.pending = self.pending[:boundary], self.pending[boundary:]
                if phrase.strip():
                    yield phrase

    def finish(self):
        tail, self.pending = self.pending, ''
        return tail if tail.strip() else ''

    def discard(self):
        self.pending = ''
