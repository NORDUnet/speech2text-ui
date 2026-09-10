import unittest
from types import SimpleNamespace
from utils.confidence_review import review_targets, review_index


def row(text, word_id, confidence=.2, start=1, end=2):
    return text, start, end, confidence, {'word_id':word_id}


class ConfidenceReviewTests(unittest.TestCase):
    def test_occurrences_not_spellings_and_wrap(self):
        captions=[SimpleNamespace(index=1,text='we we'),SimpleNamespace(index=2,text='we')]
        targets=review_targets(captions,[row('we','a'),row('we','b',.8),row('we','c')],.3)
        self.assertEqual([t['word_id'] for t in targets],['a','c'])
        self.assertEqual(targets[1]['caption_index'],2)
        self.assertEqual(review_index(targets,'a',1),1)
        self.assertEqual(review_index(targets,'c',1),0)
        self.assertEqual(review_index(targets,None,-1),1)
        self.assertEqual(review_index(targets,'a',-1),1)

    def test_unusable_timing_and_missing_confidence(self):
        rows=[row('a',None),row('b','b',None),row('c','c',start=float('nan')),
              row('d','d',start=2,end=1),row('e','e',start=-1),row('f','f',end=1),row('g','g')]
        targets=review_targets([SimpleNamespace(index=1,text='a b c d e f g')],rows,.3)
        self.assertEqual([t['word_id'] for t in targets],['g'])

    def test_threshold_change_and_removed_current(self):
        c=[SimpleNamespace(index=1,text='a b')]; rows=[row('a','a',.2),row('b','b',.4)]
        self.assertEqual(len(review_targets(c,rows,.4)),1)
        self.assertEqual(review_targets(c,rows,0),[])
        self.assertIsNone(review_index([],None,1))
        self.assertEqual(review_index(review_targets(c,rows,.5),'removed',1),0)

    def test_reviewed_occurrence_excluded_without_hiding_repeated_word(self):
        captions = [SimpleNamespace(index=1, text="one one")]
        rows = [row("one", "job:0"), row("one", "job:1")]
        self.assertEqual([t["word_id"] for t in review_targets(captions, rows, .3, {"job:0"})], ["job:1"])
        self.assertEqual(len(review_targets(captions, rows, .3, set())), 2)

    def test_split_keeps_identity_and_updates_card(self):
        rows=[row('one','a'),row('two','b')]
        c=[SimpleNamespace(index=7,text='one'),SimpleNamespace(index=8,text='two')]
        targets=review_targets(c,rows,.3)
        self.assertEqual([(t['word_id'],t['caption_index']) for t in targets],[('a',7),('b',8)])

if __name__=='__main__':unittest.main()
