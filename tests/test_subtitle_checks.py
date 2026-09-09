import unittest
from utils.caption import SRTCaption
from utils.subtitle_checks import check_subtitles

class SubtitleChecks(unittest.TestCase):
    def test_long_line_is_warning(self):
        issues = check_subtitles([SRTCaption(1,"00:00:00,000","00:00:03,000","x"*43)])
        self.assertEqual([i["severity"] for i in issues],["warning"])

    def test_short_positive_duration_is_warning(self):
        issues = check_subtitles([SRTCaption(1,"00:00:00,000","00:00:00,500","Hello")])
        self.assertEqual([i["severity"] for i in issues],["warning"])

    def test_reversed_duration_is_error(self):
        issues = check_subtitles([SRTCaption(1,"00:00:02,000","00:00:01,000","Hello")])
        self.assertEqual([i["severity"] for i in issues],["error"])

    def test_overlap_is_error(self):
        issues = check_subtitles([SRTCaption(1,"00:00:00,000","00:00:02,000","Hello"), SRTCaption(2,"00:00:01,000","00:00:03,000","World")])
        self.assertTrue(any("overlaps" in i["message"] and i["severity"]=="error" for i in issues))

    def test_valid_captions(self):
        self.assertEqual(check_subtitles([SRTCaption(1,"00:00:00,000","00:00:02,000","Hello")]),[])

if __name__ == "__main__": unittest.main()
