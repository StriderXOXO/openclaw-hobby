"""Tests for hobee/daemon.py — JSON helpers and pending shares management."""

import json
import pytest
from pathlib import Path

from hobee.daemon import BaseDaemon


# ---------------------------------------------------------------------------
# load_json()
# ---------------------------------------------------------------------------

class TestLoadJson:
    def test_nonexistent_returns_default(self, tmp_path):
        result = BaseDaemon.load_json(tmp_path / "missing.json", {"fallback": True})
        assert result == {"fallback": True}

    def test_nonexistent_no_default_returns_empty_dict(self, tmp_path):
        result = BaseDaemon.load_json(tmp_path / "missing.json")
        assert result == {}

    def test_valid_json(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text(json.dumps({"key": "值", "number": 42}))
        result = BaseDaemon.load_json(f)
        assert result["key"] == "值"
        assert result["number"] == 42

    def test_valid_json_list(self, tmp_path):
        f = tmp_path / "list.json"
        f.write_text(json.dumps([1, 2, 3]))
        result = BaseDaemon.load_json(f, [])
        assert result == [1, 2, 3]


# ---------------------------------------------------------------------------
# save_json()
# ---------------------------------------------------------------------------

class TestSaveJson:
    def test_creates_file(self, tmp_path):
        f = tmp_path / "output.json"
        BaseDaemon.save_json(f, {"hello": "世界"})
        assert f.exists()
        data = json.loads(f.read_text())
        assert data["hello"] == "世界"

    def test_creates_parent_dirs(self, tmp_path):
        f = tmp_path / "nested" / "dir" / "data.json"
        BaseDaemon.save_json(f, {"nested": True})
        assert f.exists()

    def test_preserves_chinese(self, tmp_path):
        f = tmp_path / "cn.json"
        BaseDaemon.save_json(f, {"名称": "测试播客"})
        raw = f.read_text(encoding="utf-8")
        assert "测试播客" in raw  # not escaped as \uXXXX

    def test_overwrites_existing(self, tmp_path):
        f = tmp_path / "overwrite.json"
        BaseDaemon.save_json(f, {"v": 1})
        BaseDaemon.save_json(f, {"v": 2})
        data = json.loads(f.read_text())
        assert data["v"] == 2


# ---------------------------------------------------------------------------
# Roundtrip: save → load
# ---------------------------------------------------------------------------

class TestRoundtrip:
    def test_save_then_load(self, tmp_path):
        f = tmp_path / "roundtrip.json"
        original = [
            {"id": "p-001", "title": "播客标题", "shared": False},
            {"id": "p-002", "title": "第二集", "shared": True},
        ]
        BaseDaemon.save_json(f, original)
        loaded = BaseDaemon.load_json(f, [])
        assert loaded == original
