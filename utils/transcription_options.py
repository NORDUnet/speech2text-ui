"""Validate and snapshot settings before an upload starts."""
import math


def transcription_options(language, output_format, speakers, verbatim, languages):
    if language not in languages:
        raise ValueError('Choose a supported language.')
    if output_format not in ('Transcript', 'Subtitles'):
        raise ValueError('Choose Transcript or Subtitles.')
    if isinstance(speakers, bool):
        raise ValueError('Speaker count must be a whole number, zero or greater.')
    try:
        count = float(speakers)
    except (ValueError, TypeError):
        raise ValueError('Speaker count must be a whole number, zero or greater.') from None
    if not math.isfinite(count) or count < 0 or not count.is_integer():
        raise ValueError('Speaker count must be a whole number, zero or greater.')
    return dict(language=language, output_format=output_format, speakers=int(count),
                verbatim=bool(verbatim) and language.lower() in ('swedish', 'norwegian'))
