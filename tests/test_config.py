"""Tests for hobee/config.py — configuration loading and storage auto-detection."""

import json
import os
import pytest
from pathlib import Path

from hobee.config import HobbyConfig


# ---------------------------------------------------------------------------
# get() priority
# ---------------------------------------------------------------------------

class TestGet:
    def test_env_overrides_config(self, monkeypatch, tmp_path):
        config_file = tmp_path / "podcast-hobby" / "config.json"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(json.dumps({"llm_endpoint": "from-config"}))

        monkeypatch.setenv("LLM_ENDPOINT", "from-env")
        config = HobbyConfig("podcast", workspace_root=tmp_path)
        assert config.get("llm_endpoint") == "from-env"

    def test_config_over_default(self, monkeypatch, tmp_path):
        config_file = tmp_path / "podcast-hobby" / "config.json"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(json.dumps({"custom_key": "from-config"}))

        monkeypatch.delenv("CUSTOM_KEY", raising=False)
        config = HobbyConfig("podcast", workspace_root=tmp_path)
        assert config.get("custom_key") == "from-config"
        assert config.get("custom_key", "default") == "from-config"

    def test_default_fallback(self, monkeypatch, tmp_path):
        (tmp_path / "podcast-hobby").mkdir(parents=True)
        monkeypatch.delenv("MISSING_KEY", raising=False)
        config = HobbyConfig("podcast", workspace_root=tmp_path)
        assert config.get("missing_key", "my-default") == "my-default"
        assert config.get("missing_key") is None


# ---------------------------------------------------------------------------
# require()
# ---------------------------------------------------------------------------

class TestRequire:
    def test_missing_raises(self, monkeypatch, tmp_path):
        (tmp_path / "podcast-hobby").mkdir(parents=True)
        monkeypatch.delenv("NONEXISTENT", raising=False)
        config = HobbyConfig("podcast", workspace_root=tmp_path)
        with pytest.raises(ValueError, match="Missing required config"):
            config.require("nonexistent")

    def test_present_returns(self, monkeypatch, tmp_path):
        (tmp_path / "podcast-hobby").mkdir(parents=True)
        monkeypatch.setenv("SOME_KEY", "some-value")
        config = HobbyConfig("podcast", workspace_root=tmp_path)
        assert config.require("some_key") == "some-value"


# ---------------------------------------------------------------------------
# _load_env_file()
# ---------------------------------------------------------------------------

class TestLoadEnvFile:
    def test_basic_parsing(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("MY_TEST_VAR=hello\nMY_OTHER=world\n")
        monkeypatch.delenv("MY_TEST_VAR", raising=False)
        monkeypatch.delenv("MY_OTHER", raising=False)

        (tmp_path / "podcast-hobby").mkdir(parents=True)
        HobbyConfig("podcast", workspace_root=tmp_path, env_file=env_file)
        assert os.environ.get("MY_TEST_VAR") == "hello"
        assert os.environ.get("MY_OTHER") == "world"

    def test_comments_and_blanks_skipped(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\n\nVALID_KEY=yes\n")
        monkeypatch.delenv("VALID_KEY", raising=False)

        (tmp_path / "podcast-hobby").mkdir(parents=True)
        HobbyConfig("podcast", workspace_root=tmp_path, env_file=env_file)
        assert os.environ.get("VALID_KEY") == "yes"

    def test_quotes_stripped(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("QUOTED='single'\nDOUBLE=\"double\"\n")
        monkeypatch.delenv("QUOTED", raising=False)
        monkeypatch.delenv("DOUBLE", raising=False)

        (tmp_path / "podcast-hobby").mkdir(parents=True)
        HobbyConfig("podcast", workspace_root=tmp_path, env_file=env_file)
        assert os.environ.get("QUOTED") == "single"
        assert os.environ.get("DOUBLE") == "double"

    def test_no_overwrite_existing(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING_VAR=from-file\n")
        monkeypatch.setenv("EXISTING_VAR", "already-set")

        (tmp_path / "podcast-hobby").mkdir(parents=True)
        HobbyConfig("podcast", workspace_root=tmp_path, env_file=env_file)
        assert os.environ.get("EXISTING_VAR") == "already-set"


# ---------------------------------------------------------------------------
# create_storage() auto-detection
# ---------------------------------------------------------------------------

class TestCreateStorage:
    def test_default_sqlite(self, monkeypatch, tmp_path):
        for key in ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "STORAGE_BACKEND"]:
            monkeypatch.delenv(key, raising=False)
        (tmp_path / "podcast-hobby").mkdir(parents=True)

        config = HobbyConfig("podcast", workspace_root=tmp_path)
        storage = config.create_storage()
        assert type(storage).__name__ == "SQLiteBackend"

    def test_auto_detect_feishu(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FEISHU_APP_ID", "test-id")
        monkeypatch.setenv("FEISHU_APP_SECRET", "test-secret")
        monkeypatch.setenv("FEISHU_APP_TOKEN", "test-token")
        monkeypatch.setenv("PODCAST_TABLE_ID", "test-table")
        monkeypatch.delenv("STORAGE_BACKEND", raising=False)
        (tmp_path / "podcast-hobby").mkdir(parents=True)

        config = HobbyConfig("podcast", workspace_root=tmp_path)
        storage = config.create_storage()
        assert type(storage).__name__ == "FeishuBitableBackend"

    def test_explicit_sqlite(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
        monkeypatch.setenv("FEISHU_APP_ID", "should-be-ignored")
        (tmp_path / "podcast-hobby").mkdir(parents=True)

        config = HobbyConfig("podcast", workspace_root=tmp_path)
        storage = config.create_storage()
        assert type(storage).__name__ == "SQLiteBackend"

    def test_unknown_backend_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STORAGE_BACKEND", "notion")
        (tmp_path / "podcast-hobby").mkdir(parents=True)

        config = HobbyConfig("podcast", workspace_root=tmp_path)
        with pytest.raises(ValueError, match="Unknown storage backend"):
            config.create_storage()
