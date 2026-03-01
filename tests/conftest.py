"""Shared fixtures for openclaw-hobby tests."""

import os
import sys
from pathlib import Path

import pytest

# Ensure project root is on sys.path for imports like hobee.*, triage.*, etc.
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Also add daemons/podcast for podcast_api imports
PODCAST_DIR = PROJECT_ROOT / "daemons" / "podcast"
if str(PODCAST_DIR) not in sys.path:
    sys.path.insert(0, str(PODCAST_DIR))


@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a temporary workspace directory structure."""
    ws = tmp_path / "workspace"
    (ws / "hobby" / "logs").mkdir(parents=True)
    (ws / "podcast-hobby" / "transcripts").mkdir(parents=True)
    (ws / "youtube-hobby" / "subtitles").mkdir(parents=True)
    (ws / "twitter-hobby").mkdir(parents=True)
    return ws


@pytest.fixture
def clean_env(monkeypatch):
    """Remove Feishu-related env vars to ensure clean state."""
    for key in [
        "FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_APP_TOKEN",
        "PODCAST_TABLE_ID", "YOUTUBE_TABLE_ID", "TWITTER_TABLE_ID",
        "STORAGE_BACKEND", "LLM_ENDPOINT", "LLM_API_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)
