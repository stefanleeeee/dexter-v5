"""Synthetic long RU replies only; no actual conversation, models or API."""
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import dialog_memory as dm
import test_dialog_memory as memory_tests
from test_command_states import FRAME


QUESTION = 'Расскажи про Марс'
FIRST = 'Марс — четвёртая планета Солнечной системы.'
DETAIL = 'На Марсе есть горы и равнины, его поверхность исследуют аппараты.'
LAST = 'Полёт до Марса занимает несколько месяцев.'
ANSWER = FIRST + ' ' + (DETAIL + ' ') * 80 + LAST


class CompactTests(unittest.TestCase):
    def test_short_pair_is_exactly_original(self):
        m = dm.DialogMemory()
        self.assertTrue(memory_tests.add(m, QUESTION, FIRST))
        self.assertEqual(memory_tests.history(m)[1]['content'], FIRST)
        s = m.status()
        self.assertEqual((s['memory_full_turns'], s['memory_compact_turns']), (1, 0))
        self.assertFalse(s['memory_last_compacted'])

    def test_long_reply_keeps_question_prefix_and_conclusion(self):
        m = dm.DialogMemory()
        self.assertGreater(dm.estimate_tokens(ANSWER), 1500)
        self.assertTrue(memory_tests.add(m, QUESTION, ANSWER))
        pair = memory_tests.history(m)
        self.assertEqual(pair[0]['content'], QUESTION)
        stored = pair[1]['content']
        self.assertIn(dm.COMPACT_MARKER, stored)
        self.assertIn(FIRST, stored)
        self.assertTrue(stored.endswith(LAST))
        self.assertLess(len(stored), len(ANSWER))
        s = m.status()
        self.assertEqual((s['memory_turns'], s['memory_compact_turns'], s['memory_full_turns']), (1, 1, 0))
        self.assertTrue(s['memory_last_compacted'])
        self.assertLessEqual(s['memory_tokens_est'], 1500)

    def test_extraction_only_no_new_facts(self):
        stored = dm.compact_answer(QUESTION, ANSWER, 1500)
        for line in stored.splitlines():
            if line not in (dm.COMPACT_MARKER, dm.OMITTED_MARKER):
                self.assertIn(line, ANSWER)
        self.assertTrue(stored.startswith(dm.COMPACT_MARKER))

    def test_followups_both_receive_original_mars_question(self):
        m = dm.DialogMemory(); memory_tests.add(m, QUESTION, ANSWER)
        for followup in ['А сколько до него лететь?', 'О какой планете мы говорили?']:
            messages, _, _ = m.prepare('system', followup)
            self.assertEqual(messages[1], {'role': 'user', 'content': QUESTION})
            self.assertIn('Марс', messages[2]['content'])
            self.assertEqual(messages[-1]['content'], followup)

    def test_many_long_replies_bound_budget_and_chronology(self):
        m = dm.DialogMemory(max_turns=2)
        for i in range(12):
            self.assertTrue(memory_tests.add(m, f'{QUESTION} {i}', ANSWER))
            self.assertLessEqual(m.status()['memory_tokens_est'], 1500)
            self.assertLessEqual(m.status()['memory_turns'], 2)
        questions = [x['content'] for x in memory_tests.history(m) if x['role'] == 'user']
        self.assertEqual(questions, [f'{QUESTION} 10', f'{QUESTION} 11'])
        self.assertEqual(m.status()['memory_evicted_turns'], 10)

    def test_budget_alone_evicts_whole_old_pairs(self):
        m = dm.DialogMemory()
        for i in range(10):memory_tests.add(m, f'{QUESTION} {i}', ANSWER)
        s = m.status()
        self.assertLess(s['memory_turns'], 6)
        self.assertGreater(s['memory_evicted_turns'], 0)
        pair_roles = [x['role'] for x in memory_tests.history(m)]
        self.assertEqual(pair_roles, ['user', 'assistant'] * s['memory_turns'])

    def test_clear_removes_both_types_and_resets_last_compacted(self):
        m = dm.DialogMemory();memory_tests.add(m, QUESTION, ANSWER);memory_tests.add(m, 'q', 'a')
        self.assertEqual(m.clear(), 2)
        s = m.status()
        for key in ['memory_turns', 'memory_full_turns', 'memory_compact_turns', 'memory_tokens_est']:
            self.assertEqual(s[key], 0)
        self.assertFalse(s['memory_last_compacted'])
        self.assertEqual(s['memory_last_store'], 'cleared')

    def test_unpunctuated_or_oversized_first_sentence_keeps_topic_only(self):
        for text in ['слово ' * 2000, 'слово ' * 2000 + '. Конец.']:
            m = dm.DialogMemory();self.assertTrue(memory_tests.add(m, QUESTION, text))
            self.assertEqual(memory_tests.history(m)[1]['content'], dm.TOPIC_ONLY_MARKER)
            self.assertEqual(memory_tests.history(m)[0]['content'], QUESTION)

    def test_tiny_budget_skips_with_diagnostic(self):
        m = dm.DialogMemory(max_tokens=35)
        self.assertFalse(memory_tests.add(m, QUESTION, ANSWER))
        s = m.status()
        self.assertEqual(s['memory_skipped_turns'], 1)
        self.assertEqual(s['memory_last_store'], 'skipped_budget')
        self.assertEqual(s['memory_turns'], 0)

    def test_unfinished_tail_and_abbreviations_not_used_as_conclusion(self):
        text = 'Марс изучают, т.е. исследуют аппаратами. ' + (DETAIL + ' ') * 80 + 'Неоконченный хвост'
        stored = dm.compact_answer(QUESTION, text, 1500)
        self.assertIn('т.е. исследуют аппаратами.', stored)
        self.assertNotIn('Неоконченный хвост', stored)

    def test_original_snapshot_is_not_rewritten(self):
        m = dm.DialogMemory();memory_tests.add(m, 'q', 'original')
        messages, version, _ = m.prepare('s', QUESTION)
        saved = [dict(x) for x in messages]
        m.commit(version, QUESTION, ANSWER)
        self.assertEqual(messages, saved)

    def test_context_guard_keeps_previous_strict_byte_protection(self):
        m = dm.DialogMemory();memory_tests.add(m, QUESTION, ANSWER)
        text = 's' * (dm.MODEL_CONTEXT_TOKENS-dm.CONTEXT_RESERVE-2048-33)
        messages, _, metrics = m.prepare(text, 'q')
        self.assertEqual(len(messages), 2)
        self.assertEqual(metrics['memory_turns'], 0)
        self.assertLess(metrics['memory_context_tokens_est'], metrics['memory_context_tokens_bound'])
        with self.assertRaises(ValueError):m.prepare(text + 'xx', 'q')

    def test_estimates_are_approximate_and_do_not_use_usage(self):
        self.assertLess(dm.estimate_tokens('Марс ' * 100), dm.context_token_bound('Марс ' * 100))
        self.assertEqual(dm.estimate_tokens('a' * 30), 26)
        self.assertEqual(dm.estimate_tokens('я' * 30), 31)
        self.assertGreater(dm.estimate_tokens('🙂' * 30), dm.estimate_tokens('a' * 30))

    def test_clear_racing_compact_commit_never_restores_after_clear_returns(self):
        m = dm.DialogMemory();entered = threading.Event();release = threading.Event();cleared = threading.Event()
        version = m.version();original = dm.compact_answer
        def compact(*args):
            entered.set()
            if not release.wait(2):raise RuntimeError('checkpoint timeout')
            return original(*args)
        with patch.object(dm, 'compact_answer', compact):
            worker = threading.Thread(target=lambda:m.commit(version, QUESTION, ANSWER))
            cleaner = threading.Thread(target=lambda:(m.clear(), cleared.set()))
            worker.start();self.assertTrue(entered.wait(2));cleaner.start()
            release.set();worker.join(2);cleaner.join(2)
            self.assertFalse(worker.is_alive());self.assertFalse(cleaner.is_alive());self.assertTrue(cleared.is_set())
        self.assertEqual(m.status()['memory_turns'], 0)
        self.assertFalse(m.commit(version, QUESTION, ANSWER))

    def test_both_modes_full_audio_and_console_text_not_replaced(self):
        for streaming in [False, True]:
            process, env, stt, llm, tts = memory_tests.IntegrationTests().pipeline(streaming)
            stt.return_value = NS(text=QUESTION)
            llm.return_value = NS(choices=[NS(message=NS(content=ANSWER), finish_reason='stop')])
            env['stream_reply'].return_value = (ANSWER, {'outcome': 'done'})
            process(Mock(), FRAME, {'mode': 'PROCESSING'})
            if not streaming:self.assertEqual(tts.call_args.args[1], ANSWER)
            self.assertTrue(any(ANSWER in str(call.args) for call in env['log'].call_args_list))
            self.assertEqual(env['dialog_memory'].status()['memory_compact_turns'], 1)
            stt.return_value = NS(text='А сколько до него лететь?')
            process(Mock(), FRAME, {'mode': 'PROCESSING'})
            messages = env['stream_reply'].call_args.args[2] if streaming else llm.call_args.kwargs['messages']
            self.assertEqual(messages[1]['content'], QUESTION)
            self.assertIn(dm.COMPACT_MARKER, messages[2]['content'])
            self.assertEqual(stt.call_count, 2)
            self.assertEqual((env['stream_reply'] if streaming else llm).call_count, 2)
            if streaming:llm.assert_not_called();tts.assert_not_called()

    def test_cancel_errors_and_partial_do_not_create_compact_records(self):
        for outcome in ['partial_length', 'empty_length', 'cancelled', 'error']:
            process, env, *_ = memory_tests.IntegrationTests().pipeline(True)
            env['stream_reply'].return_value = (ANSWER, {'outcome': outcome})
            process(Mock(), FRAME, {'mode': 'PROCESSING'})
            self.assertEqual(env['dialog_memory'].status()['memory_turns'], 0)
            self.assertFalse(env['dialog_memory'].status()['memory_last_compacted'])


if __name__ == '__main__':
    unittest.main()
