"""Configuration tests use synthetic credentials only; no models or network."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from local_config import Config, load_config, redact_log


class ConfigTests(unittest.TestCase):
    def load(self, text='', env=None):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.env'
            path.write_text(text, encoding='utf-8')
            return load_config(path, {} if env is None else env)

    def test_missing_required_setting(self):
        with self.assertRaisesRegex(ValueError, 'Set GROQ_API_KEY'):
            self.load()

    def test_missing_file_and_environment_only(self):
        config = load_config(Path('nonexistent-test-config.env'), {'GROQ_API_KEY': 'synthetic'})
        self.assertEqual((config.host, config.port), ('0.0.0.0', 3333))

    def test_local_configuration_and_environment_override(self):
        config = self.load('GROQ_API_KEY="synthetic local"\nHOST=127.0.0.1\nPORT=4321\n',
                           {'GROQ_API_KEY': 'synthetic override'})
        self.assertEqual((config.api_key, config.host, config.port), ('synthetic override', '127.0.0.1', 4321))

    def test_quotes_hash_equals_and_comments(self):
        config = self.load('# comment\nGROQ_API_KEY=\'test#=value\'\n')
        self.assertEqual(config.api_key, 'test#=value')

    def test_invalid_file_does_not_expose_value(self):
        for text in ('GROQ_API_KEY="synthetic-unclosed', 'BAD=synthetic-private', 'synthetic-private'):
            with self.assertRaises(ValueError) as error:
                self.load(text)
            self.assertNotIn('synthetic', str(error.exception))

    def test_invalid_ports(self):
        for value in ('0', '-1', '65536', 'bad'):
            with self.assertRaisesRegex(ValueError, 'PORT'):
                self.load('GROQ_API_KEY=synthetic\nPORT=' + value)

    def test_invalid_host_and_placeholder(self):
        for text in ('GROQ_API_KEY=YOUR_GROQ_API_KEY', 'GROQ_API_KEY=synthetic\nHOST='):
            with self.assertRaises(ValueError): self.load(text)

    def test_repr_and_redaction(self):
        config = Config('synthetic-private', '0.0.0.0', 3333)
        self.assertNotIn(config.api_key, repr(config))
        self.assertEqual(redact_log('key synthetic-private peer 192.0.2.10 gsk_SYNTHETIC', config),
                         'key [REDACTED] peer [IP] [REDACTED]')

    def test_loading_never_prints_configuration(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.load('GROQ_API_KEY=synthetic-private')
        self.assertEqual(output.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
