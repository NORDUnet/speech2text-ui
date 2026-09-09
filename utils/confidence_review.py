"""Build review targets from occurrence-matched, currently displayed words."""
import math


def review_targets(captions, rows, threshold, reviewed=()):
    targets = []
    offset = 0
    seen = set()
    for caption in captions:
        count = len(caption.text.split())
        for text, start, end, confidence, provenance in rows[offset:offset + count]:
            word_id = provenance.get('word_id')
            values = (start, end, confidence)
            if not word_id or word_id in seen or word_id in reviewed or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                for v in values
            ):
                continue
            if start < 0 or end <= start or not 0 <= confidence < threshold:
                continue
            seen.add(word_id)
            targets.append(dict(word_id=word_id, text=text, start=start, end=end,
                                confidence=confidence, caption_index=caption.index))
        offset += count
    return targets


def review_index(targets, current_id, direction):
    """Navigate occurrences in transcript order, wrapping at either end."""
    if not targets:
        return None
    current = next((i for i, t in enumerate(targets) if t['word_id'] == current_id), None)
    if current is None:
        return 0 if direction >= 0 else len(targets) - 1
    return (current + direction) % len(targets)
