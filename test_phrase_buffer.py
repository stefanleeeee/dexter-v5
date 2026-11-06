import unittest
from phrase_buffer import PhraseBuffer, PhraseLimitError


class PhraseTests(unittest.TestCase):
    def split(self, deltas):
        buffer = PhraseBuffer()
        phrases = [p for delta in deltas for p in buffer.feed(delta)]
        tail = buffer.finish()
        if tail: phrases.append(tail)
        return phrases

    def test_split_inside_words_and_punctuation(self):
        deltas = ['Py','thon и Git','Hub работают','.',' Следующее',' предложение','!']
        phrases = self.split(deltas)
        self.assertEqual(''.join(phrases), ''.join(deltas))
        self.assertEqual(phrases, ['Python и GitHub работают. ', 'Следующее предложение!'])

    def test_many_sentences_in_delta(self):
        text = 'Первое. Второе! Третье? Четвёртое… Конец.'
        self.assertEqual(''.join(self.split([text])), text)
        self.assertEqual(len(self.split([text])), 5)

    def test_abbreviations_decimals_initials_urls(self):
        text = 'В г. Москве А. С. Пушкин стоит 3.14 руб. Это т. е. пример Python3.11 и https://GitHub.com/API. Готово!'
        phrases = self.split(list(text))
        self.assertEqual(''.join(phrases), text)
        self.assertNotIn('В г. ', phrases)
        self.assertNotIn('14 руб. ', phrases)
        self.assertFalse(any(p.endswith('А. ') or p.endswith('С. ') for p in phrases))

    def test_quotes_and_ellipsis_between_deltas(self):
        text = 'Он сказал «Готово!» Потом… всё.'
        self.assertEqual(''.join(self.split(list(text))), text)

    def test_long_unpunctuated_text_preserved_without_word_splitting(self):
        text = 'Python и GitHub помогают работать ' * 150
        phrases = self.split([text])
        self.assertEqual(''.join(phrases), text)
        self.assertTrue(all(len(p) <= 900 for p in phrases))
        self.assertTrue(all(p[-1].isspace() for p in phrases[:-1]))

    def test_long_word_fails_explicitly(self):
        buffer = PhraseBuffer()
        with self.assertRaises(PhraseLimitError): list(buffer.feed('x' * 901))
        self.assertLessEqual(buffer.high_water, 900)

    def test_empty_and_unterminated_tail(self):
        self.assertEqual(self.split(['', '   ']), [])
        self.assertEqual(self.split(['Незавершённый', ' текст']), ['Незавершённый текст'])

    def test_cancel_discards_pending(self):
        buffer = PhraseBuffer()
        list(buffer.feed('Не говорить'))
        buffer.discard()
        self.assertEqual(buffer.finish(), '')

    def test_safe_comma_boundary(self):
        text = 'Длинная мысль ' * 32 + ', затем продолжение без потери текста.'
        phrases = self.split([text])
        self.assertEqual(''.join(phrases), text)
        self.assertTrue(phrases[0].rstrip().endswith(','))


if __name__ == '__main__': unittest.main()
