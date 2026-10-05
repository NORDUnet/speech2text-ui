"""Parse explicit multi-value claims without splitting literal commas."""
import json


def parse_claim_input(value: str) -> str | list[str]:
    if not value.lstrip().startswith("["):
        return value
    try:
        result = json.loads(value)
    except ValueError:
        raise ValueError('Use a valid JSON list, for example ["staff", "member"].')
    if not isinstance(result, list) or any(not isinstance(v, str) for v in result):
        raise ValueError("Attribute lists must contain text values only.")
    return result
