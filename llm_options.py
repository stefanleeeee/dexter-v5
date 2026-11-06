"""Shared request parameters and numeric-only usage extraction (no credentials)."""


def completion_options(config=None, *, streaming=False):
    options = dict(model='openai/gpt-oss-120b', temperature=0.7,
                   max_completion_tokens=getattr(config, 'llm_max_completion_tokens', 2048),
                   reasoning_effort=getattr(config, 'llm_reasoning_effort', 'low'),
                   include_reasoning=getattr(config, 'llm_include_reasoning', False))
    if streaming:
        options['stream'] = True
        # Groq SDK1.7 accepts extra_body, but has no named stream_options argument.
        options['extra_body'] = {'stream_options': {'include_usage': True}}
    return options


def _get(obj, key):
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def usage_numbers(usage):
    def number(obj, key):
        value = _get(obj, key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    return dict(prompt_tokens=number(usage, 'prompt_tokens'),
                completion_tokens=number(usage, 'completion_tokens'),
                reasoning_tokens=number(_get(usage, 'completion_tokens_details'), 'reasoning_tokens'),
                total_tokens=number(usage, 'total_tokens'))
