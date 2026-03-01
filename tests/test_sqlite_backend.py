"""Tests for hobee/storage/sqlite.py — SQLite storage backend."""

import os
import pytest
from pathlib import Path

from hobee.storage.sqlite import SQLiteBackend


@pytest.fixture
def backend(tmp_path):
    db_path = str(tmp_path / "test.db")
    return SQLiteBackend(db_path)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class TestSchema:
    def test_creates_db_file(self, tmp_path):
        db_path = str(tmp_path / "new.db")
        SQLiteBackend(db_path)
        assert os.path.exists(db_path)

    def test_idempotent(self, tmp_path):
        db_path = str(tmp_path / "idem.db")
        b1 = SQLiteBackend(db_path)
        b2 = SQLiteBackend(db_path)  # second init should not fail
        assert b2 is not b1

    def test_creates_media_dir(self, tmp_path):
        db_path = str(tmp_path / "m.db")
        b = SQLiteBackend(db_path)
        assert os.path.isdir(b.media_dir)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

class TestCRUD:
    def test_create_and_find(self, backend):
        rid = backend.create_record({"编号": "guid-001", "名称": "测试节目"})
        assert rid.startswith("rec_")

        found = backend.find_record("编号", "guid-001")
        assert found is not None
        assert found["record_id"] == rid
        assert found["fields"]["名称"] == "测试节目"

    def test_find_nonexistent(self, backend):
        assert backend.find_record("编号", "does-not-exist") is None

    def test_update_merges_fields(self, backend):
        rid = backend.create_record({"编号": "guid-002", "名称": "原始"})
        backend.update_record(rid, {"名称": "更新后", "摘要": "新增字段"})

        found = backend.find_record("编号", "guid-002")
        assert found["fields"]["名称"] == "更新后"
        assert found["fields"]["摘要"] == "新增字段"
        assert found["fields"]["编号"] == "guid-002"  # untouched

    def test_update_nonexistent_raises(self, backend):
        with pytest.raises(ValueError, match="Record not found"):
            backend.update_record("rec_nonexistent", {"x": "y"})

    def test_find_record_by_guid(self, backend):
        rid = backend.create_record({"编号": "guid-003"})
        assert backend.find_record_by_guid("guid-003") == rid
        assert backend.find_record_by_guid("no-such-guid") is None


# ---------------------------------------------------------------------------
# list_records
# ---------------------------------------------------------------------------

class TestListRecords:
    def test_list_all(self, backend):
        backend.create_record({"编号": "a"})
        backend.create_record({"编号": "b"})
        records = backend.list_records()
        assert len(records) == 2

    def test_list_empty(self, backend):
        assert backend.list_records() == []

    def test_list_with_filter(self, backend):
        backend.create_record({"编号": "x", "来源": "podcast"})
        backend.create_record({"编号": "y", "来源": "twitter"})
        results = backend.list_records(filter_expr='来源=podcast')
        assert len(results) == 1
        assert results[0]["fields"]["来源"] == "podcast"

    def test_list_page_size(self, backend):
        for i in range(5):
            backend.create_record({"编号": f"item-{i}"})
        results = backend.list_records(page_size=3)
        assert len(results) == 3


# ---------------------------------------------------------------------------
# upload_media
# ---------------------------------------------------------------------------

class TestUploadMedia:
    def test_copies_file(self, backend, tmp_path):
        src = tmp_path / "audio.mp3"
        src.write_bytes(b"fake audio data")
        result_path = backend.upload_media(str(src), "audio.mp3")
        assert os.path.exists(result_path)
        assert open(result_path, "rb").read() == b"fake audio data"

    def test_duplicate_name_no_overwrite(self, backend, tmp_path):
        src1 = tmp_path / "f1.txt"
        src1.write_text("content 1")
        src2 = tmp_path / "f2.txt"
        src2.write_text("content 2")

        path1 = backend.upload_media(str(src1), "same.txt")
        path2 = backend.upload_media(str(src2), "same.txt")
        assert path1 != path2
        assert open(path1).read() == "content 1"
        assert open(path2).read() == "content 2"
