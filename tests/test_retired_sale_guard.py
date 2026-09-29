"""Retired catalog products cannot start new checkout."""

import pytest

from app.entitlements import (
    assert_course_available_for_purchase,
    is_retired_from_sale,
)


def test_retired_slugs_detected():
    assert is_retired_from_sale("week-zero-reset")
    assert is_retired_from_sale("deep-calm-protocol")
    assert is_retired_from_sale("focus-discipline")
    assert is_retired_from_sale("starter-bundle")
    assert not is_retired_from_sale("mindful-foundations")
    assert not is_retired_from_sale("")


def test_assert_blocks_retired_purchase():
    with pytest.raises(ValueError, match="no longer for sale"):
        assert_course_available_for_purchase("starter-bundle")


def test_assert_allows_other_or_empty():
    assert_course_available_for_purchase("mindful-foundations")
    assert_course_available_for_purchase(None)
    assert_course_available_for_purchase("")
