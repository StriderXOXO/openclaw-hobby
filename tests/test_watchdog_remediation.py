"""Tests for watchdog.py — auto-remediation logic (pure functions)."""

import time
from unittest.mock import patch

import pytest

# Import only the pure functions we need, avoiding module-level side effects.
# watchdog.py reads env vars at import time, which is fine (they default to "").
from watchdog.watchdog import (
    reset_daily_counters,
    can_restart,
    record_restart,
    can_fix_corrupt,
    record_corrupt_fix,
    MAX_RESTARTS_PER_DAY,
    RESTART_COOLDOWN_SEC,
    MAX_CORRUPT_FIX_PER_DAY,
)


# ---------------------------------------------------------------------------
# reset_daily_counters()
# ---------------------------------------------------------------------------

class TestResetDailyCounters:
    def test_same_day_no_reset(self):
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        state = {
            "date": today,
            "restarts": {"svc-a": {"count": 2, "last": 100}},
            "corrupt_fixes": {"file.json": {"count": 1}},
        }
        result = reset_daily_counters(state)
        assert result["restarts"]["svc-a"]["count"] == 2
        assert result["corrupt_fixes"]["file.json"]["count"] == 1

    def test_new_day_resets(self):
        state = {
            "date": "2020-01-01",
            "restarts": {"svc-a": {"count": 3, "last": 100}},
            "corrupt_fixes": {"file.json": {"count": 5}},
        }
        result = reset_daily_counters(state)
        assert result["restarts"] == {}
        assert result["corrupt_fixes"] == {}
        # date should be updated to today
        from datetime import datetime
        assert result["date"] == datetime.now().strftime("%Y-%m-%d")

    def test_missing_date_field(self):
        state = {"restarts": {}, "corrupt_fixes": {}}
        result = reset_daily_counters(state)
        from datetime import datetime
        assert result["date"] == datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# can_restart()
# ---------------------------------------------------------------------------

class TestCanRestart:
    def test_first_restart_allowed(self):
        state = {"restarts": {}}
        assert can_restart(state, "my-service") is True

    def test_at_daily_limit(self):
        state = {
            "restarts": {
                "my-service": {"count": MAX_RESTARTS_PER_DAY, "last": 0}
            }
        }
        assert can_restart(state, "my-service") is False

    def test_below_limit_but_in_cooldown(self):
        state = {
            "restarts": {
                "my-service": {"count": 1, "last": time.time()}
            }
        }
        assert can_restart(state, "my-service") is False

    def test_below_limit_and_past_cooldown(self):
        state = {
            "restarts": {
                "my-service": {
                    "count": 1,
                    "last": time.time() - RESTART_COOLDOWN_SEC - 1,
                }
            }
        }
        assert can_restart(state, "my-service") is True

    def test_different_service_unaffected(self):
        state = {
            "restarts": {
                "other-service": {"count": MAX_RESTARTS_PER_DAY, "last": time.time()}
            }
        }
        assert can_restart(state, "my-service") is True

    def test_at_limit_minus_one(self):
        state = {
            "restarts": {
                "my-service": {
                    "count": MAX_RESTARTS_PER_DAY - 1,
                    "last": time.time() - RESTART_COOLDOWN_SEC - 1,
                }
            }
        }
        assert can_restart(state, "my-service") is True


# ---------------------------------------------------------------------------
# record_restart()
# ---------------------------------------------------------------------------

class TestRecordRestart:
    def test_new_service(self):
        state = {"restarts": {}}
        record_restart(state, "new-svc")
        assert state["restarts"]["new-svc"]["count"] == 1
        assert state["restarts"]["new-svc"]["last"] > 0

    def test_existing_service_increments(self):
        state = {"restarts": {"svc": {"count": 2, "last": 100}}}
        record_restart(state, "svc")
        assert state["restarts"]["svc"]["count"] == 3
        assert state["restarts"]["svc"]["last"] > 100

    def test_does_not_affect_other_services(self):
        state = {"restarts": {"other": {"count": 5, "last": 999}}}
        record_restart(state, "new")
        assert state["restarts"]["other"]["count"] == 5


# ---------------------------------------------------------------------------
# can_fix_corrupt()
# ---------------------------------------------------------------------------

class TestCanFixCorrupt:
    def test_first_fix_allowed(self):
        state = {"corrupt_fixes": {}}
        assert can_fix_corrupt(state, "/path/to/file.json") is True

    def test_at_daily_limit(self):
        state = {
            "corrupt_fixes": {
                "/path/to/file.json": {"count": MAX_CORRUPT_FIX_PER_DAY}
            }
        }
        assert can_fix_corrupt(state, "/path/to/file.json") is False

    def test_different_file_unaffected(self):
        state = {
            "corrupt_fixes": {
                "/other/file.json": {"count": MAX_CORRUPT_FIX_PER_DAY}
            }
        }
        assert can_fix_corrupt(state, "/new/file.json") is True


# ---------------------------------------------------------------------------
# record_corrupt_fix()
# ---------------------------------------------------------------------------

class TestRecordCorruptFix:
    def test_new_file(self):
        state = {"corrupt_fixes": {}}
        record_corrupt_fix(state, "/path/to/file.json")
        assert state["corrupt_fixes"]["/path/to/file.json"]["count"] == 1

    def test_existing_file_increments(self):
        state = {"corrupt_fixes": {"/f.json": {"count": 0}}}
        record_corrupt_fix(state, "/f.json")
        assert state["corrupt_fixes"]["/f.json"]["count"] == 1
