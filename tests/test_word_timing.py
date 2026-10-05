"""Pure sequence matching tests; no UI server or transcription required."""
import unittest
from utils.word_timing import match_word_indices


class WordTimingTests(unittest.TestCase):
    def match(self, source, edited):
        return match_word_indices([{"t": t} for t in source.split()], edited.split())

    def test_equal_count_replacement(self):
        self.assertEqual(self.match("we build services", "we improve services"), [0, None, 2])

    def test_insertion_and_deletion(self):
        self.assertEqual(self.match("we build services", "we now build services"), [0, None, 1, 2])
        self.assertEqual(self.match("we now build services", "we build services"), [0, 2, 3])

    def test_repeated_occurrences(self):
        self.assertEqual(self.match("we build and we share", "we improve and we share"), [0, None, 2, 3, 4])

    def test_split_merge_and_punctuation(self):
        self.assertEqual(self.match("So we build services", "So, we\nbuild services."), [0, 1, 2, 3])

    def test_no_match(self):
        self.assertEqual(self.match("old text", "entirely different"), [None, None])

    def test_undo_restores_original_indices(self):
        self.match("we build services", "we change services")
        self.assertEqual(self.match("we build services", "we build services"), [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
