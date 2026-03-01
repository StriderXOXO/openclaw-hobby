"""Tests for podcast_api.py — duration parsing and formatting."""

import pytest

from podcast_api import _parse_duration, format_duration


# ---------------------------------------------------------------------------
# _parse_duration()
# ---------------------------------------------------------------------------

class TestParseDuration:
    @pytest.mark.parametrize("raw, expected", [
        ("1:30:45", 5445),      # HH:MM:SS
        ("0:45:30", 2730),      # HH:MM:SS with leading zero hour
        ("45:30", 2730),        # MM:SS
        ("2:00", 120),          # MM:SS
        ("120", 120),           # raw seconds as string
        ("0", 0),               # zero
        ("3600", 3600),         # one hour in seconds
    ])
    def test_valid_formats(self, raw, expected):
        assert _parse_duration(raw) == expected

    @pytest.mark.parametrize("raw", [
        "",
        None,
        "invalid",
        "not:a:time",
        "abc",
    ])
    def test_invalid_returns_zero(self, raw):
        assert _parse_duration(raw) == 0

    def test_two_part_colon(self):
        assert _parse_duration("10:30") == 630  # 10 min 30 sec

    def test_three_part_colon(self):
        assert _parse_duration("2:15:00") == 8100  # 2h 15m


# ---------------------------------------------------------------------------
# format_duration()
# ---------------------------------------------------------------------------

class TestFormatDuration:
    @pytest.mark.parametrize("seconds, expected", [
        (5445, "1:30:45"),
        (120, "2:00"),
        (3600, "1:00:00"),
        (61, "1:01"),
        (0, "unknown"),
        (None, "unknown"),
    ])
    def test_formatting(self, seconds, expected):
        assert format_duration(seconds) == expected

    def test_roundtrip(self):
        """parse → format → parse should be idempotent."""
        original = "1:30:45"
        seconds = _parse_duration(original)
        formatted = format_duration(seconds)
        assert _parse_duration(formatted) == seconds
