"""Subtitle timing errors and non-blocking readability recommendations."""
import math


def check_subtitles(captions, line_limit=42):
    issues = []
    intervals = []
    seen = {}
    def add(severity, message, *indices):
        issues.append({"severity": severity, "message": message, "indices": indices})
    for caption in captions:
        i = caption.index
        if not caption.text.strip():
            add("error", f"Caption #{i} has no text to display.", i)
        for line in caption.text.split("\n"):
            if len(line) > line_limit:
                add("warning", f"Caption #{i} has a {len(line)}-character line. Recommended: {line_limit} or fewer for readability; longer lines may wrap differently across players. This does not make the SRT file invalid.", i)
                break
        try:
            start, end = caption.get_start_seconds(), caption.get_end_seconds()
            if not math.isfinite(start) or not math.isfinite(end) or start < 0:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            add("error", f"Caption #{i} has an invalid timestamp.", i)
            intervals.append(None)
            continue
        intervals.append((start, end))
        if end <= start:
            add("error", f"Caption #{i} must end after it starts.", i)
        elif end-start < .8:
            add("warning", f"Caption #{i} appears for only {end-start:.2f} seconds. It may be difficult to read; consider extending it or merging it with another caption.", i)
        if start in seen:
            add("error", f"Captions #{seen[start]} and #{i} start at the same time and may display together.", seen[start], i)
        else:
            seen[start] = i
    for offset in range(len(captions)-1):
        current, following = intervals[offset:offset+2]
        if current and following and current[1] > following[0]:
            a, b = captions[offset].index, captions[offset+1].index
            add("error", f"Caption #{a} overlaps with caption #{b}. They may display simultaneously; review their timing.", a, b)
    return issues
