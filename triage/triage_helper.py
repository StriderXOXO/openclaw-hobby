#!/usr/bin/env python3
"""
Triage Helper — Analyze pending hobby content via LLM and update storage backend.

Reads pending-shares.json for a hobby, finds untriaged items, calls an LLM
for analysis, updates storage records, and marks items as triaged.

Configuration (via environment variables or config.json):
    FEISHU_APP_ID       Feishu app ID
    FEISHU_APP_SECRET   Feishu app secret
    FEISHU_APP_TOKEN    Feishu bitable app token
    PODCAST_TABLE_ID    Table ID for podcast records
    YOUTUBE_TABLE_ID    Table ID for YouTube records
    TWITTER_TABLE_ID    Table ID for Twitter records
    LLM_ENDPOINT        LLM API endpoint (Anthropic-compatible messages API)
    LLM_API_KEY         LLM API key
    LLM_MODEL           LLM model name (default: claude-sonnet-4-20250514)

Usage:
  # Triage podcast items
  python3 triage_helper.py podcast --batch-size 5

  # Triage youtube items
  python3 triage_helper.py youtube --batch-size 5

  # Triage twitter items
  python3 triage_helper.py twitter --batch-size 10

  # Dry run (show what would be processed)
  python3 triage_helper.py podcast --dry-run

  # Show untriaged counts
  python3 triage_helper.py status

  # Backfill triaged flag on existing items
  python3 triage_helper.py backfill-flag podcast
"""

import argparse
import json
import os
import sys
import time

import requests

# Add parent directory so we can import from the hobee package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hobee.config import HobbyConfig

# --- LLM Configuration ---

LLM_ENDPOINT = os.environ.get("LLM_ENDPOINT", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-sonnet-4-20250514")

# --- Hobby Configuration ---

# Table IDs are loaded from environment variables; the keys below map hobby
# names to content-specific settings.  Storage backends are created per-hobby
# via HobbyConfig.create_storage() which reads FEISHU_APP_ID / APP_SECRET /
# APP_TOKEN and the per-hobby TABLE_ID from env vars.

HOBBY_CONFIG = {
    "podcast": {
        "table_id_env": "PODCAST_TABLE_ID",
        "content_path_key": "transcript_path",
        "content_type": "转录",
        "full_analysis": True,
    },
    "youtube": {
        "table_id_env": "YOUTUBE_TABLE_ID",
        "content_path_key": "subtitles_path",
        "content_type": "字幕",
        "full_analysis": True,
    },
    "twitter": {
        "table_id_env": "TWITTER_TABLE_ID",
        "content_path_key": None,  # Twitter uses inline text
        "content_type": "推文",
        "full_analysis": False,  # Twitter only gets summary + topic_tags
    },
}

# --- LLM Prompt Templates ---
# These are the core value of triage — structured Chinese prompts that produce
# high-quality LLM analysis output.

ANALYSIS_PROMPT = """你是一位资深内容分析师。请对以下{content_type}进行深度分析。

## 内容标题
{title}

## {content_type}全文
{content}

## 要求

请输出以下四个字段，每个字段用 === 分隔：

=== 摘要 ===
用中文撰写2-3句话的摘要。第一句点明主题，第二句概括核心观点，第三句（可选）说明独特价值。不要用"本期节目讨论了…"这种套话。

=== 亮点 ===
用中文撰写3-5个要点，每个以"- "开头。每个要点必须具体、有信息量。
好的例子："- 涂津豪在高中期间进入DeepSeek实习，参与了R1推理模型的早期训练实验"
坏的例子："- 嘉宾分享了AI经历"

=== 精选原文 ===
从原文中精选2-3句最有价值的原话，用引号包裹。必须是原文中实际存在的句子。

=== 主题标签 ===
3-6个主题标签，用逗号分隔。混合中英文。例如：AI安全, DeepSeek, 开源模型, reinforcement learning
"""

TWITTER_ANALYSIS_PROMPT = """你是一位资深Twitter/X内容分析师。请对以下推文进行分析。

## 用户
{user}

## 推文内容
{content}

## 要求

请输出以下两个字段，每个字段用 === 分隔：

=== 摘要 ===
用中文撰写1-2句话的摘要，概括推文核心观点或信息。语气简洁直接。

=== 主题标签 ===
2-4个主题标签，用逗号分隔。混合中英文。例如：AI安全, OpenAI, scaling law
"""


# --- LLM API ---

def call_llm(prompt, max_tokens=2000):
    """Call the LLM endpoint (Anthropic-compatible messages API)."""
    if not LLM_ENDPOINT or not LLM_API_KEY:
        raise RuntimeError(
            "LLM not configured. Set LLM_ENDPOINT and LLM_API_KEY environment variables."
        )
    r = requests.post(
        LLM_ENDPOINT,
        headers={
            "Authorization": f"Bearer {LLM_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": LLM_MODEL,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    # Find the text block (API may return thinking + text blocks)
    for block in data.get("content", []):
        if block.get("type") == "text" and "text" in block:
            return block["text"]
    raise RuntimeError(f"No text block in LLM response: {data}")


def parse_analysis(text):
    """Parse LLM output into structured fields using === delimiters."""
    sections = {}
    current_section = None
    current_lines = []

    for line in text.split("\n"):
        if line.strip().startswith("=== ") and line.strip().endswith(" ==="):
            if current_section:
                sections[current_section] = "\n".join(current_lines).strip()
            current_section = line.strip().strip("= ").strip()
            current_lines = []
        elif current_section:
            current_lines.append(line)

    if current_section:
        sections[current_section] = "\n".join(current_lines).strip()

    return {
        "摘要": sections.get("摘要", ""),
        "亮点": sections.get("亮点", ""),
        "精选原文": sections.get("精选原文", ""),
        "主题标签": sections.get("主题标签", ""),
    }


# --- File helpers ---

def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# --- Item filtering ---

def get_pending_shares_path(hobby):
    """Get the pending-shares.json path for a hobby."""
    config = HobbyConfig(hobby)
    return str(config.pending_shares_file)


def is_untriaged(item, config):
    """Check if an item needs triage."""
    # Explicit triaged flag takes precedence
    if item.get("triaged") is True:
        return False

    # Must have a record_id to update storage
    if not item.get("record_id"):
        return False

    # For podcast/youtube: must have content file
    content_key = config["content_path_key"]
    if content_key:
        content_path = item.get(content_key)
        if not content_path or not os.path.exists(content_path):
            return False
    else:
        # Twitter: must have text
        if not item.get("text"):
            return False

    return True


def get_item_title(item, hobby):
    """Get a human-readable title for a content item."""
    if hobby == "twitter":
        user = item.get("user", "unknown")
        text = (item.get("text") or "")[:80]
        return f"@{user}: {text}"
    return item.get("title") or item.get("episode_title") or item.get("name", "untitled")


def get_item_content(item, config):
    """Get full content for analysis."""
    content_key = config["content_path_key"]
    if content_key:
        content_path = item.get(content_key)
        if content_path and os.path.exists(content_path):
            with open(content_path, encoding="utf-8") as f:
                content = f.read()
            # Truncate to ~30K chars to stay within LLM context
            if len(content) > 30000:
                content = content[:30000] + "\n\n[...内容过长，已截断...]"
            return content
    else:
        # Twitter: inline text + thread preview
        parts = [item.get("text", "")]
        thread = item.get("thread_preview", [])
        if thread:
            parts.append("\n--- 线程 ---")
            for t in thread:
                parts.append(t if isinstance(t, str) else str(t))
        return "\n".join(parts)
    return None


# --- Storage helpers ---

def create_storage_backend(hobby):
    """Create a storage backend for the given hobby.

    Uses HobbyConfig to load Feishu credentials from environment variables.
    """
    config = HobbyConfig(hobby)
    return config.create_storage()


# --- Commands ---

def cmd_triage(hobby, batch_size=5, dry_run=False):
    """Triage untriaged items for a given hobby."""
    config = HOBBY_CONFIG[hobby]
    pending_path = get_pending_shares_path(hobby)
    shares = load_json(pending_path, [])

    candidates = [item for item in shares if is_untriaged(item, config)]
    print(f"[{hobby}] Found {len(candidates)} untriaged items (out of {len(shares)} total)")

    batch = candidates[:batch_size]
    print(f"[{hobby}] Processing batch of {len(batch)} items")

    if not batch:
        return

    if dry_run:
        for c in batch:
            title = get_item_title(c, hobby)
            print(f"  - {title[:70]} (record: {c.get('record_id', 'none')})")
        return

    # Create storage backend for bitable updates
    storage = create_storage_backend(hobby)

    updated = 0
    errors = 0

    for i, item in enumerate(batch):
        title = get_item_title(item, hobby)
        print(f"\n[{i+1}/{len(batch)}] Analyzing: {title[:60]}...")

        content = get_item_content(item, config)
        if not content:
            print(f"  No content available, skipping")
            errors += 1
            continue

        # Call LLM
        try:
            if config["full_analysis"]:
                prompt = ANALYSIS_PROMPT.format(
                    content_type=config["content_type"],
                    title=title,
                    content=content,
                )
                response = call_llm(prompt)
                fields = parse_analysis(response)
            else:
                # Twitter: simpler analysis
                prompt = TWITTER_ANALYSIS_PROMPT.format(
                    user=item.get("user", "unknown"),
                    content=content,
                )
                response = call_llm(prompt, max_tokens=500)
                fields = parse_analysis(response)
                # Twitter doesn't have highlights/picked_sentences
                fields["亮点"] = ""
                fields["精选原文"] = ""
        except Exception as e:
            print(f"  LLM error: {e}")
            errors += 1
            continue

        if not fields["摘要"]:
            print(f"  LLM returned empty analysis, skipping")
            errors += 1
            continue

        print(f"  摘要: {fields['摘要'][:80]}...")
        print(f"  主题标签: {fields['主题标签']}")

        # Update storage record
        record_id = item.get("record_id")
        bitable_fields = {"摘要": fields["摘要"], "主题标签": fields["主题标签"]}
        if config["full_analysis"]:
            bitable_fields["亮点"] = fields["亮点"]
            bitable_fields["精选原文"] = fields["精选原文"]

        try:
            storage.update_record(record_id, bitable_fields)
        except Exception as e:
            print(f"  Storage update error: {e}")
            errors += 1
            continue

        # Update pending-shares.json item
        item["summary"] = fields["摘要"]
        item["topic_tags"] = fields["主题标签"]
        if config["full_analysis"]:
            item["highlights"] = fields["亮点"]
            item["picked_sentences"] = fields["精选原文"]
        item["triaged"] = True

        updated += 1
        time.sleep(2)  # rate limit between LLM calls

    # Save updated pending-shares
    save_json(pending_path, shares)

    print(f"\n=== Done ===")
    print(f"Updated: {updated}, Errors: {errors}")
    remaining = len(candidates) - len(batch)
    if remaining > 0:
        print(f"Remaining untriaged: {remaining}")


def cmd_status():
    """Show untriaged counts for all hobbies."""
    print("=== Triage Status ===\n")
    total_untriaged = 0
    for hobby, config in HOBBY_CONFIG.items():
        pending_path = get_pending_shares_path(hobby)
        shares = load_json(pending_path, [])
        untriaged = sum(1 for item in shares if is_untriaged(item, config))
        triaged = sum(1 for item in shares if item.get("triaged") is True)
        total = len(shares)
        total_untriaged += untriaged
        print(f"  {hobby:10s}: {total:3d} total, {triaged:3d} triaged, {untriaged:3d} untriaged")
    print(f"\n  Total untriaged: {total_untriaged}")


def cmd_backfill_flag(hobby):
    """Set triaged flag on existing items based on whether they have real analysis."""
    config = HOBBY_CONFIG[hobby]
    pending_path = get_pending_shares_path(hobby)
    shares = load_json(pending_path, [])

    already_triaged = 0
    set_true = 0
    set_false = 0

    for item in shares:
        if item.get("triaged") is True:
            already_triaged += 1
            continue

        # Check if item has real analysis (not just raw preview)
        summary = item.get("summary", "")
        has_topic_tags = bool(item.get("topic_tags"))

        # Heuristic: real analysis summaries are structured Chinese text, shorter than raw previews
        # Raw previews are typically the first N chars of transcript/subtitle
        has_real_analysis = (
            summary
            and has_topic_tags
            and len(summary) < 500
            and "。" in summary
        )

        if has_real_analysis:
            item["triaged"] = True
            set_true += 1
        else:
            item["triaged"] = False
            set_false += 1

    save_json(pending_path, shares)
    print(f"[{hobby}] Backfill complete:")
    print(f"  Already triaged: {already_triaged}")
    print(f"  Set triaged=true (has analysis): {set_true}")
    print(f"  Set triaged=false (needs triage): {set_false}")


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Triage Helper — analyze hobby content via LLM")
    sub = parser.add_subparsers(dest="command")

    # Triage commands for each hobby
    batch_defaults = {"podcast": 5, "youtube": 5, "twitter": 10}
    for hobby in ["podcast", "youtube", "twitter"]:
        p = sub.add_parser(hobby, help=f"Triage {hobby} items")
        p.add_argument("--batch-size", type=int, default=batch_defaults.get(hobby, 5),
                        help="Items per batch")
        p.add_argument("--dry-run", action="store_true", help="Show what would be processed")

    # Status
    sub.add_parser("status", help="Show untriaged counts for all hobbies")

    # Backfill flag
    p_backfill = sub.add_parser("backfill-flag", help="Set triaged flag on existing items")
    p_backfill.add_argument("hobby", choices=["podcast", "youtube", "twitter"],
                            help="Which hobby to backfill")

    args = parser.parse_args()

    if args.command in ("podcast", "youtube", "twitter"):
        cmd_triage(args.command, batch_size=args.batch_size, dry_run=args.dry_run)
    elif args.command == "status":
        cmd_status()
    elif args.command == "backfill-flag":
        cmd_backfill_flag(args.hobby)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
