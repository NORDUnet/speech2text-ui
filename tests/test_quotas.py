from decimal import InvalidOperation

import pytest
from utils.quotas import hours_to_seconds


@pytest.mark.parametrize(("hours", "seconds"), [("0", 0), ("1", 3600), ("1.25", 4500), ("0.01", 36), ("2500", 9000000)])
def test_hours_configuration_is_exact(hours, seconds):
    assert hours_to_seconds(hours) == seconds


@pytest.mark.parametrize("hours", ["-1", "NaN", "Infinity", "0.00001", "", "abc"])
def test_invalid_hours_are_rejected(hours):
    with pytest.raises((ValueError, InvalidOperation)):
        hours_to_seconds(hours)
