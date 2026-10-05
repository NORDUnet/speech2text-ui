"""Match edited transcript tokens to original word occurrences."""
from difflib import SequenceMatcher


def normalize(text):
    return text.strip(".,!?…:;\"'").lower()


def match_word_indices(words, tokens):
    """Return source indices, or None for unmatched display words.

    Match the full sequence so card splits/merges cannot change ownership.
    Disable the frequent-word heuristic: common words still need timings.
    Indices identify occurrences, not just spellings.
    """
    source = [normalize(w["t"]) for w in words]
    target = [normalize(t) for t in tokens]
    matches = [None] * len(tokens)
    for block in SequenceMatcher(None, source, target, autojunk=False).get_matching_blocks():
        for offset in range(block.size):
            if target[block.b + offset]:
                matches[block.b + offset] = block.a + offset
    return matches
