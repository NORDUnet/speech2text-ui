import unittest
from utils.transcription_options import transcription_options

class OptionsTests(unittest.TestCase):
    def options(self, language='English', speakers=0, verbatim=False):
        return transcription_options(language, 'Subtitles', speakers, verbatim, ['English', 'Swedish', 'Norwegian'])

    def test_hidden_verbatim_does_not_leak_to_other_languages(self):
        self.assertFalse(self.options(verbatim=True)['verbatim'])
        self.assertTrue(self.options(language='Norwegian', verbatim=True)['verbatim'])

    def test_invalid_speaker_counts_rejected(self):
        for value in [-1, 1.5, None, True, float('nan'), float('inf'), 'invalid']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.options(speakers=value)

    def test_language_and_format_validation(self):
        with self.assertRaises(ValueError): self.options(language='unsupported')
        with self.assertRaises(ValueError): transcription_options('English', 'bad', 0, False, ['English'])

    def test_independent_snapshots(self):
        first, second = self.options(speakers=2), self.options(speakers=3)
        second['speakers'] = 4
        self.assertEqual(first['speakers'], 2)
