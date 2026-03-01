"""Tests for triage_helper.py — LLM output parsing and item filtering."""

import os
import pytest

from triage.triage_helper import parse_analysis, is_untriaged, get_item_title


# ---------------------------------------------------------------------------
# parse_analysis()
# ---------------------------------------------------------------------------

class TestParseAnalysis:
    def test_full_output(self):
        text = (
            "=== 摘要 ===\n"
            "这是关于AI安全的深度讨论。主持人和嘉宾探讨了对齐问题。\n"
            "\n"
            "=== 亮点 ===\n"
            "- DeepSeek R1 采用了新的强化学习训练方法\n"
            "- 开源模型在安全评测中表现优于闭源\n"
            "\n"
            "=== 精选原文 ===\n"
            '"我认为开源是解决AI安全的唯一路径"\n'
            "\n"
            "=== 主题标签 ===\n"
            "AI安全, DeepSeek, 开源模型, reinforcement learning"
        )
        result = parse_analysis(text)
        assert "AI安全" in result["摘要"]
        assert "DeepSeek R1" in result["亮点"]
        assert "开源是解决" in result["精选原文"]
        assert "AI安全" in result["主题标签"]

    def test_missing_sections(self):
        text = (
            "=== 摘要 ===\n"
            "简短摘要。\n"
            "\n"
            "=== 主题标签 ===\n"
            "AI, 科技"
        )
        result = parse_analysis(text)
        assert result["摘要"] == "简短摘要。"
        assert result["亮点"] == ""
        assert result["精选原文"] == ""
        assert result["主题标签"] == "AI, 科技"

    def test_empty_input(self):
        result = parse_analysis("")
        assert result == {"摘要": "", "亮点": "", "精选原文": "", "主题标签": ""}

    def test_no_delimiters(self):
        result = parse_analysis("This is just plain text without any delimiters.")
        assert result == {"摘要": "", "亮点": "", "精选原文": "", "主题标签": ""}

    def test_reordered_sections(self):
        text = (
            "=== 主题标签 ===\n"
            "标签A, 标签B\n"
            "\n"
            "=== 摘要 ===\n"
            "这是摘要。\n"
        )
        result = parse_analysis(text)
        assert result["主题标签"] == "标签A, 标签B"
        assert result["摘要"] == "这是摘要。"

    def test_multiline_section(self):
        text = (
            "=== 亮点 ===\n"
            "- 要点一\n"
            "- 要点二\n"
            "- 要点三\n"
            "\n"
            "=== 主题标签 ===\n"
            "tag"
        )
        result = parse_analysis(text)
        assert result["亮点"].count("- ") == 3

    def test_whitespace_in_delimiter(self):
        """Delimiter format: === 摘要 === with possible leading/trailing spaces."""
        text = "  === 摘要 ===  \n内容\n"
        result = parse_analysis(text)
        assert result["摘要"] == "内容"


# ---------------------------------------------------------------------------
# is_untriaged()
# ---------------------------------------------------------------------------

PODCAST_CONFIG = {
    "table_id_env": "PODCAST_TABLE_ID",
    "content_path_key": "transcript_path",
    "content_type": "转录",
    "full_analysis": True,
}

TWITTER_CONFIG = {
    "table_id_env": "TWITTER_TABLE_ID",
    "content_path_key": None,
    "content_type": "推文",
    "full_analysis": False,
}


class TestIsUntriaged:
    def test_already_triaged(self):
        item = {"triaged": True, "record_id": "rec1", "transcript_path": "/tmp/x"}
        assert is_untriaged(item, PODCAST_CONFIG) is False

    def test_no_record_id(self):
        item = {"triaged": False, "transcript_path": "/tmp/x"}
        assert is_untriaged(item, PODCAST_CONFIG) is False

    def test_podcast_with_valid_transcript(self, tmp_path):
        f = tmp_path / "transcript.txt"
        f.write_text("some content")
        item = {"record_id": "rec1", "transcript_path": str(f)}
        assert is_untriaged(item, PODCAST_CONFIG) is True

    def test_podcast_transcript_missing_file(self):
        item = {"record_id": "rec1", "transcript_path": "/nonexistent/path.txt"}
        assert is_untriaged(item, PODCAST_CONFIG) is False

    def test_twitter_with_text(self):
        item = {"record_id": "rec1", "text": "Hello world"}
        assert is_untriaged(item, TWITTER_CONFIG) is True

    def test_twitter_no_text(self):
        item = {"record_id": "rec1"}
        assert is_untriaged(item, TWITTER_CONFIG) is False


# ---------------------------------------------------------------------------
# get_item_title()
# ---------------------------------------------------------------------------

class TestGetItemTitle:
    def test_twitter(self):
        item = {"user": "elonmusk", "text": "AI is the future" + "x" * 200}
        title = get_item_title(item, "twitter")
        assert title.startswith("@elonmusk: AI is the future")

    def test_podcast_title(self):
        item = {"title": "Episode 42: Deep Learning"}
        assert get_item_title(item, "podcast") == "Episode 42: Deep Learning"

    def test_podcast_episode_title_fallback(self):
        item = {"episode_title": "Fallback Title"}
        assert get_item_title(item, "podcast") == "Fallback Title"

    def test_podcast_name_fallback(self):
        item = {"name": "Last Resort Name"}
        assert get_item_title(item, "podcast") == "Last Resort Name"
