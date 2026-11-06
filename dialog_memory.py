"""Bounded, process-local completed dialogue. Never stores audio or SDK objects."""
from collections import deque
from dataclasses import dataclass
import re
import threading

# Groq GPT-OSS-120B: https://console.groq.com/docs/model/openai/gpt-oss-120b
MODEL_CONTEXT_TOKENS = 131072
CONTEXT_RESERVE = 1024


def estimate_tokens(text):
    """History heuristic, NOT tokenization/usage: 3 ASCII or 2 Cyrillic chars/token.

    Other scripts/symbols cost their UTF-8 bytes. +16/message for overhead.
    Values can over/underestimate BPE; context_token_bound guards the window.
    """
    units = sum(2 if ord(c) < 128 else 3 if '\u0400' <= c <= '\u052f'
                else 6 * len(c.encode('utf-8')) for c in text)
    return (units + 5) // 6 + 16


def context_token_bound(text):
    """Retain the previous pessimistic byte budget for model-window protection."""
    return len(text.encode('utf-8')) + 16


COMPACT_MARKER = '[Сокращённая запись ответа: часть текста опущена; это не смысловая суммаризация.]'
OMITTED_MARKER = '[Часть исходного ответа опущена.]'
TOPIC_ONLY_MARKER = '[Ответ сокращён: подробности не сохранены.]'
# No new tuning required: compact pairs target <=512 estimated tokens, but a
# whole first sentence may use the full history budget when necessary.
COMPACT_TARGET = 512


def _sentence_ends(text):
    """Conservative sentence boundaries; never fabricate or cut a word/clause."""
    abbreviations = {'г', 'ул', 'им', 'др', 'рис', 'см', 'стр', 'т', 'е', 'д', 'п',
                     'e', 'g', 'i', 'mr', 'mrs', 'dr', 'etc'}
    for match in re.finditer(r'[.!?…]+[»"\x27)\]]*(?=\s|$)', text):
        if match.group().startswith('.'):
            word = re.search(r'(\w+)$', text[:match.start()])
            word = word.group() if word else ''
            if word.casefold() in abbreviations or (len(word) == 1 and word.isupper()):
                continue
        yield match.end()


def compact_answer(user, assistant, budget):
    """Extract a prefix and optionally a final sentence; keep the question intact."""
    answer = assistant.strip()
    ends = list(_sentence_ends(answer))
    def fits(text, limit=budget):
        return estimate_tokens(user) + estimate_tokens(text) <= limit
    # Exclude a sole final boundary: adding a marker to a full oversized answer
    # cannot reduce its size. A trailing unfinished fragment is not a conclusion.
    candidates = [end for end in ends if end < len(answer)]
    if candidates:
        first = answer[:candidates[0]]
        compact = COMPACT_MARKER + '\n' + first
        if fits(compact):
            target = max(estimate_tokens(user) + estimate_tokens(compact),
                         min(COMPACT_TARGET, budget))
            prefix_end = candidates[0]
            suffix = ''
            # Prefer a real concluding sentence over filling the whole prefix.
            if ends[-1] == len(answer) and len(ends) > 1:
                last_start = ends[-2]
                tail = answer[last_start:].strip()
                proposed = compact + '\n' + OMITTED_MARKER + '\n' + tail
                if last_start > prefix_end and fits(proposed, target):
                    suffix = '\n' + OMITTED_MARKER + '\n' + tail
            for end in candidates[1:]:
                if suffix and end >= ends[-2]:
                    break
                proposed = COMPACT_MARKER + '\n' + answer[:end] + suffix
                if not fits(proposed, target):
                    break
                prefix_end = end
            return COMPACT_MARKER + '\n' + answer[:prefix_end] + suffix
    # The original question still retains the topic; do not turn an incomplete
    # first sentence into a misleading assertion. No answer facts are invented.
    return TOPIC_ONLY_MARKER if fits(TOPIC_ONLY_MARKER) else None


@dataclass(frozen=True)
class _Turn:
    user: str
    assistant: str
    tokens: int
    context_tokens: int
    compacted: bool


class DialogMemory:
    def __init__(self, enabled=True, max_turns=6, max_tokens=1500):
        if not isinstance(enabled, bool):
            raise ValueError('Memory enabled must be boolean')
        if type(max_turns) is not int or not 1 <= max_turns <= 100:
            raise ValueError('Memory max_turns must be 1..100')
        if type(max_tokens) is not int or not 1 <= max_tokens <= 32768:
            raise ValueError('Memory max_tokens must be 1..32768')
        self.enabled, self.max_turns, self.max_tokens = enabled, max_turns, max_tokens
        self._turns = deque()
        self._tokens = self._version = self._evicted = 0
        self._context_tokens = self._skipped = 0
        self._last_compacted = False
        self._last_store = 'initial'
        self._lock = threading.Lock()

    def _trim(self, budget, context_budget=MODEL_CONTEXT_TOKENS):
        while self._turns and (len(self._turns) > self.max_turns or self._tokens > budget
                               or self._context_tokens > context_budget):
            removed = self._turns.popleft()
            self._tokens -= removed.tokens
            self._context_tokens -= removed.context_tokens
            self._evicted += 1

    def prepare(self, system, user, completion_tokens=2048):
        fixed = estimate_tokens(system) + estimate_tokens(user)
        fixed_bound = context_token_bound(system) + context_token_bound(user)
        available = MODEL_CONTEXT_TOKENS - CONTEXT_RESERVE - completion_tokens - fixed_bound
        if available < 0:
            raise ValueError('Current request exceeds conservative model context budget')
        with self._lock:
            self._trim(self.max_tokens, available)
            messages = [{'role': 'system', 'content': system}]
            for turn in self._turns:
                messages.extend(({'role': 'user', 'content': turn.user},
                                 {'role': 'assistant', 'content': turn.assistant}))
            messages.append({'role': 'user', 'content': user})
            metrics = self._status()
            metrics['memory_context_tokens_est'] = fixed + self._tokens
            metrics['memory_context_tokens_bound'] = fixed_bound + self._context_tokens
            return messages, self._version, metrics

    def commit(self, version, user, assistant):
        if not user.strip() or not assistant.strip():
            return False
        with self._lock:
            if not self.enabled or version != self._version:
                return False
            self._version += 1
            tokens = estimate_tokens(user) + estimate_tokens(assistant)
            compacted = tokens > self.max_tokens
            self._last_compacted = False
            if compacted:
                assistant = compact_answer(user, assistant, self.max_tokens)
                if assistant is None:
                    self._skipped += 1
                    self._last_store = 'skipped_budget'
                    return False
                tokens = estimate_tokens(user) + estimate_tokens(assistant)
            context_tokens = context_token_bound(user) + context_token_bound(assistant)
            self._turns.append(_Turn(user, assistant, tokens, context_tokens, compacted))
            self._tokens += tokens
            self._context_tokens += context_tokens
            self._trim(self.max_tokens)
            self._last_compacted = compacted
            self._last_store = 'stored_compact' if compacted else 'stored_full'
            return bool(self._turns)

    def clear(self):
        with self._lock:
            removed = len(self._turns)
            self._turns.clear()
            self._tokens = 0
            self._context_tokens = 0
            self._last_compacted = False
            self._last_store = 'cleared'
            self._version += 1
            return removed

    def version(self):
        with self._lock:
            return self._version

    def _status(self):
        compact = sum(turn.compacted for turn in self._turns)
        return dict(memory_enabled=self.enabled, memory_turns=len(self._turns),
                    memory_full_turns=len(self._turns) - compact,
                    memory_compact_turns=compact,
                    memory_last_compacted=self._last_compacted,
                    memory_last_store=self._last_store, memory_skipped_turns=self._skipped,
                    memory_tokens_est=self._tokens, memory_evicted_turns=self._evicted,
                    memory_max_turns=self.max_turns, memory_max_tokens=self.max_tokens)

    def status(self):
        with self._lock:
            return self._status()

    def command(self, argument, log):
        argument = argument.strip().casefold()
        if argument == 'clear':
            log(f'[MEMORY] Cleared {self.clear()} turns')
        elif argument == 'status':
            log('[MEMORY] ' + str(self.status()))
        else:
            log('[MEMORY] memory status | memory clear')
