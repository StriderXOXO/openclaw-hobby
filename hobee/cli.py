#!/usr/bin/env python3
"""hobee CLI — 统一入口，一条命令体验兴趣策展系统。

Usage:
    hobee demo                     # 零配置体验：订阅示例播客 → 采集 → 展示
    hobee status                   # 各兴趣源采集/分析/待分享数量
    hobee podcast daemon           # 运行播客守护进程
    hobee podcast subscribe <url>  # 订阅播客 RSS
    hobee podcast search <query>   # 搜索播客（iTunes）
    hobee triage <hobby>           # 运行 LLM 内容分析
    hobee setup                    # 交互式配置向导
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .config import HobbyConfig, DEFAULT_WORKSPACE_ROOT


# ---------------------------------------------------------------------------
# Demo command
# ---------------------------------------------------------------------------

DEMO_SUBSCRIPTIONS = [
    {
        "name": "声东击西",
        "rss_url": "https://etw.fm/rss",
    },
    {
        "name": "疯投圈",
        "rss_url": "https://crazy.capital/feed",
    },
    {
        "name": "Lex Fridman Podcast",
        "rss_url": "https://lexfridman.com/feed/podcast/",
    },
]


def cmd_demo(args):
    """零配置 demo：订阅示例播客 → 采集一次 → 终端展示。"""
    print("\n  hobee demo — 30 秒体验兴趣策展系统\n")
    print("  正在初始化 workspace...")

    config = HobbyConfig("podcast")
    config.workspace.mkdir(parents=True, exist_ok=True)

    # 1. Write demo subscriptions
    subs_file = config.workspace / "subscriptions.json"
    if not subs_file.exists() or json.loads(subs_file.read_text()) == []:
        subs_file.write_text(json.dumps(DEMO_SUBSCRIPTIONS, indent=2, ensure_ascii=False))
        print(f"  已订阅 {len(DEMO_SUBSCRIPTIONS)} 个示例播客:")
        for s in DEMO_SUBSCRIPTIONS:
            print(f"    - {s['name']}")
    else:
        print(f"  使用已有订阅 ({subs_file})")

    # 2. Create storage (SQLite by default)
    storage = config.create_storage()
    print(f"  存储后端: {type(storage).__name__}")

    # 3. Collect
    print("\n  开始采集...\n")

    try:
        import feedparser  # noqa: F401
    except ImportError:
        print("  错误：缺少 feedparser。请运行: pip install feedparser")
        sys.exit(1)

    # Import podcast modules
    daemon_dir = Path(__file__).parent.parent / "daemons" / "podcast"
    if daemon_dir.exists():
        sys.path.insert(0, str(daemon_dir))

    try:
        from podcast_api import parse_rss, format_duration
    except ImportError:
        print("  错误：找不到 podcast_api 模块。请确保 daemons/podcast/ 目录存在。")
        sys.exit(1)

    subs = json.loads(subs_file.read_text())
    seen_file = config.workspace / "seen-episodes.json"
    seen = {}
    if seen_file.exists():
        seen = json.loads(seen_file.read_text())

    collected = []
    for sub in subs:
        name = sub["name"]
        print(f"  [{name}] 解析 RSS...")
        try:
            episodes = parse_rss(sub["rss_url"])
        except Exception as e:
            print(f"  [{name}] 解析失败: {e}")
            continue

        new_count = 0
        for ep in episodes:
            if new_count >= 2:  # Demo only takes 2 per feed
                break
            if ep["guid"] in seen:
                continue

            seen[ep["guid"]] = time.time()
            new_count += 1

            # Store in SQLite
            guid = ep["guid"]
            record_id = storage.find_record_by_guid(guid)
            if not record_id:
                record_id = storage.create_record({
                    "编号": guid[:200],
                    "名称": ep["title"],
                    "播客名称": name,
                })

            duration = format_duration(ep.get("duration", 0))
            collected.append({
                "podcast": name,
                "title": ep["title"],
                "duration": duration,
                "record_id": record_id,
            })

        if new_count:
            print(f"  [{name}] 采集到 {new_count} 个新节目")
        else:
            print(f"  [{name}] 无新内容")

    # Save seen
    seen_file.write_text(json.dumps(seen, indent=2))

    # 4. Display results
    print("\n" + "=" * 60)
    if collected:
        print(f"\n  采集完成！共 {len(collected)} 个新节目:\n")
        for i, item in enumerate(collected, 1):
            print(f"  {i}. [{item['podcast']}] {item['title']}")
            print(f"     时长: {item['duration']}")
            print()
    else:
        print("\n  本次无新内容（可能已采集过）。")
        print("  提示：删除 ~/.openclaw/workspace/podcast-hobby/seen-episodes.json 可重新采集。\n")

    print("=" * 60)
    print("\n  下一步:\n")
    print("    hobee podcast subscribe <RSS_URL>   # 添加你喜欢的播客")
    print("    hobee podcast daemon                # 启动持续采集")
    print("    hobee status                        # 查看采集状态")
    print("    hobee setup                         # 交互式配置（LLM 分析、飞书等）")
    print()


# ---------------------------------------------------------------------------
# Status command
# ---------------------------------------------------------------------------

def cmd_status(args):
    """显示各兴趣源的采集/分析/待分享状态。"""
    print("\n  hobee 系统状态\n")

    hobbies = ["podcast", "youtube", "twitter"]
    for hobby in hobbies:
        config = HobbyConfig(hobby)
        pending_file = config.pending_shares_file
        if not pending_file.exists():
            print(f"  {hobby:10s}: 未初始化")
            continue

        items = json.loads(pending_file.read_text())
        total = len(items)
        triaged = sum(1 for i in items if i.get("triaged"))
        shared = sum(1 for i in items if i.get("shared"))
        pending = total - shared

        print(f"  {hobby:10s}: {total:3d} 总计 | {triaged:3d} 已分析 | {shared:3d} 已分享 | {pending:3d} 待分享")

    # Mind state
    hobby_dir = DEFAULT_WORKSPACE_ROOT / "hobby"
    mind_state_file = hobby_dir / "mind-state.json"
    if mind_state_file.exists():
        ms = json.loads(mind_state_file.read_text())
        shared_today = ms.get("sharing", {}).get("items_shared_today", 0)
        last_share = ms.get("sharing", {}).get("last_share", "从未")
        print(f"\n  今日已分享: {shared_today}")
        print(f"  上次分享: {last_share}")

    # Storage backend info
    db_paths = []
    for hobby in hobbies:
        db = DEFAULT_WORKSPACE_ROOT / f"{hobby}-hobby" / "data.db"
        if db.exists():
            db_paths.append(str(db))
    if db_paths:
        print(f"\n  SQLite 数据库: {len(db_paths)} 个")

    print()


# ---------------------------------------------------------------------------
# Podcast subcommands
# ---------------------------------------------------------------------------

def cmd_podcast(args):
    """Podcast 子命令分发。"""
    config = HobbyConfig("podcast")
    config.workspace.mkdir(parents=True, exist_ok=True)

    if args.podcast_cmd == "daemon":
        _podcast_daemon(config)
    elif args.podcast_cmd == "subscribe":
        _podcast_subscribe(config, args.url, args.name)
    elif args.podcast_cmd == "search":
        _podcast_search(args.query, args.limit)
    elif args.podcast_cmd == "list":
        _podcast_list(config)
    else:
        print("Unknown podcast command. Use: daemon, subscribe, search, list")


def _podcast_daemon(config):
    """启动播客采集守护进程。"""
    daemon_dir = Path(__file__).parent.parent / "daemons" / "podcast"
    sys.path.insert(0, str(daemon_dir))
    try:
        from daemon import PodcastDaemon  # noqa: E402
    except ImportError:
        print("错误：找不到 daemons/podcast/daemon.py")
        sys.exit(1)

    storage = config.create_storage()
    daemon = PodcastDaemon(config, storage)
    print(f"播客守护进程启动 (存储: {type(storage).__name__})")
    daemon.run_forever()


def _podcast_subscribe(config, url, name=None):
    """订阅播客 RSS。"""
    subs_file = config.workspace / "subscriptions.json"
    subs = json.loads(subs_file.read_text()) if subs_file.exists() else []

    for s in subs:
        if s["rss_url"] == url:
            print(f"已订阅: {s['name']} ({url})")
            return

    if not name:
        try:
            import feedparser
            feed = feedparser.parse(url)
            name = feed.feed.get("title", url)
        except Exception:
            name = url

    subs.append({"name": name, "rss_url": url})
    subs_file.write_text(json.dumps(subs, indent=2, ensure_ascii=False))
    print(f"已订阅: {name}")
    print(f"  RSS: {url}")


def _podcast_search(query, limit=10):
    """搜索播客。"""
    daemon_dir = Path(__file__).parent.parent / "daemons" / "podcast"
    sys.path.insert(0, str(daemon_dir))
    try:
        from podcast_api import search_itunes
    except ImportError:
        print("错误：找不到 podcast_api 模块")
        sys.exit(1)

    results = search_itunes(query, max_results=limit)
    if not results:
        print(f"未找到: {query}")
        return
    print(f"\n找到 {len(results)} 个播客:\n")
    for i, r in enumerate(results, 1):
        print(f"  {i}. {r['title']}")
        if r["author"]:
            print(f"     作者: {r['author']}")
        print(f"     RSS: {r['rss_url']}")
        print()
    print("订阅: hobee podcast subscribe <RSS_URL>")


def _podcast_list(config):
    """列出已订阅播客。"""
    subs_file = config.workspace / "subscriptions.json"
    subs = json.loads(subs_file.read_text()) if subs_file.exists() else []
    if not subs:
        print("暂无订阅。试试: hobee podcast search <关键词>")
        return
    print(f"\n已订阅 {len(subs)} 个播客:\n")
    for i, s in enumerate(subs, 1):
        print(f"  {i}. {s['name']}")
        print(f"     RSS: {s['rss_url']}")


# ---------------------------------------------------------------------------
# Triage command
# ---------------------------------------------------------------------------

def cmd_triage(args):
    """运行 LLM 内容分析。"""
    triage_dir = Path(__file__).parent.parent / "triage"
    sys.path.insert(0, str(triage_dir))
    # Also add project root for hobee imports
    sys.path.insert(0, str(Path(__file__).parent.parent))
    try:
        from triage_helper import cmd_triage as do_triage
    except ImportError:
        print("错误：找不到 triage/triage_helper.py")
        sys.exit(1)

    do_triage(args.hobby, batch_size=args.batch_size, dry_run=args.dry_run)


# ---------------------------------------------------------------------------
# Setup wizard
# ---------------------------------------------------------------------------

def cmd_setup(args):
    """交互式配置向导。"""
    print("\n  hobee setup — 交互式配置向导\n")
    env_lines = []

    # Storage backend
    print("  存储后端:")
    print("    1. SQLite（默认，零配置）")
    print("    2. 飞书多维表格（需要企业自建应用）")
    choice = input("  选择 [1]: ").strip() or "1"

    if choice == "2":
        env_lines.append("STORAGE_BACKEND=feishu")
        print("\n  飞书配置（在 https://open.feishu.cn 创建企业自建应用）:")
        app_id = input("  FEISHU_APP_ID: ").strip()
        app_secret = input("  FEISHU_APP_SECRET: ").strip()
        app_token = input("  FEISHU_APP_TOKEN: ").strip()
        chat_id = input("  FEISHU_CHAT_ID (群聊推送，可选): ").strip()
        podcast_table = input("  PODCAST_TABLE_ID: ").strip()
        youtube_table = input("  YOUTUBE_TABLE_ID (可选): ").strip()
        twitter_table = input("  TWITTER_TABLE_ID (可选): ").strip()
        if app_id:
            env_lines.append(f"FEISHU_APP_ID={app_id}")
        if app_secret:
            env_lines.append(f"FEISHU_APP_SECRET={app_secret}")
        if app_token:
            env_lines.append(f"FEISHU_APP_TOKEN={app_token}")
        if chat_id:
            env_lines.append(f"FEISHU_CHAT_ID={chat_id}")
        if podcast_table:
            env_lines.append(f"PODCAST_TABLE_ID={podcast_table}")
        if youtube_table:
            env_lines.append(f"YOUTUBE_TABLE_ID={youtube_table}")
        if twitter_table:
            env_lines.append(f"TWITTER_TABLE_ID={twitter_table}")

    # LLM
    print("\n  LLM 配置（用于智能内容分析，可选）:")
    llm_key = input("  LLM_API_KEY (留空跳过): ").strip()
    if llm_key:
        print("  LLM 提供商:")
        print("    1. Anthropic (api.anthropic.com)")
        print("    2. DeepSeek (api.deepseek.com)")
        print("    3. 自定义（兼容 Anthropic Messages API）")
        llm_choice = input("  选择 [1]: ").strip() or "1"
        if llm_choice == "1":
            env_lines.append("LLM_ENDPOINT=https://api.anthropic.com/v1/messages")
        elif llm_choice == "2":
            env_lines.append("LLM_ENDPOINT=https://api.deepseek.com/v1/messages")
        else:
            endpoint = input("  LLM_ENDPOINT: ").strip()
            if endpoint:
                env_lines.append(f"LLM_ENDPOINT={endpoint}")
        env_lines.append(f"LLM_API_KEY={llm_key}")
        model = input("  LLM_MODEL (默认 claude-sonnet-4-20250514): ").strip()
        if model:
            env_lines.append(f"LLM_MODEL={model}")

    # Whisper
    print("\n  Whisper API（播客音频转录，可选）:")
    whisper = input("  WHISPER_ENDPOINT (留空跳过): ").strip()
    if whisper:
        env_lines.append(f"WHISPER_ENDPOINT={whisper}")
        token = input("  WHISPER_TOKEN: ").strip()
        if token:
            env_lines.append(f"WHISPER_TOKEN={token}")

    # Twitter
    print("\n  Twitter API（推文采集，可选）:")
    twitter_key = input("  TWITTER_API_KEY (留空跳过): ").strip()
    if twitter_key:
        env_lines.append(f"TWITTER_API_KEY={twitter_key}")

    # Write .env
    project_root = Path(__file__).parent.parent
    env_file = project_root / ".env"
    if env_lines:
        content = "# Generated by hobee setup\n" + "\n".join(env_lines) + "\n"
        env_file.write_text(content)
        print(f"\n  已保存配置到 {env_file}")
    else:
        print("\n  未配置任何凭证（使用默认 SQLite 存储）。")

    # Generate agent files
    _generate_agent_files(env_lines)

    # Run setup.sh workspace creation
    print("\n  初始化 workspace 目录...")
    for hobby in ["podcast", "youtube", "twitter"]:
        hc = HobbyConfig(hobby)
        hc.workspace.mkdir(parents=True, exist_ok=True)
    (DEFAULT_WORKSPACE_ROOT / "hobby" / "logs").mkdir(parents=True, exist_ok=True)

    print("\n  配置完成！\n")
    print("  下一步:")
    print("    hobee demo                    # 快速体验")
    print("    hobee podcast subscribe <url> # 订阅播客")
    print("    hobee podcast daemon          # 启动采集")
    print()


def _generate_agent_files(env_lines):
    """从 .env 配置生成 agent 指令文件。"""
    project_root = Path(__file__).parent.parent
    agent_dir = project_root / "agent"
    clawd_dir = Path.home() / "clawd"

    if not agent_dir.exists():
        return

    # Parse env lines into dict
    env_dict = {}
    for line in env_lines:
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            env_dict[k.strip()] = v.strip()

    # Mapping of placeholders to env vars
    replacements = {
        "{{WHISPER_ENDPOINT}}": env_dict.get("WHISPER_ENDPOINT", "（未配置 — 跳过音频转录功能）"),
        "{{WHISPER_TOKEN}}": env_dict.get("WHISPER_TOKEN", ""),
        "{{FEISHU_APP_TOKEN}}": env_dict.get("FEISHU_APP_TOKEN", "（未配置 — 飞书功能不可用）"),
        "{{PODCAST_TABLE_ID}}": env_dict.get("PODCAST_TABLE_ID", "（未配置）"),
        "{{YOUTUBE_TABLE_ID}}": env_dict.get("YOUTUBE_TABLE_ID", "（未配置）"),
        "{{TWITTER_TABLE_ID}}": env_dict.get("TWITTER_TABLE_ID", "（未配置）"),
        "{{CHAT_ID}}": env_dict.get("FEISHU_CHAT_ID", "（未配置 — 飞书推送不可用）"),
    }

    clawd_dir.mkdir(parents=True, exist_ok=True)

    for md_file in ["HEARTBEAT.md", "TOOLS.md", "SOUL.md"]:
        src = agent_dir / md_file
        if not src.exists():
            continue
        content = src.read_text()
        for placeholder, value in replacements.items():
            content = content.replace(placeholder, value)
        dst = clawd_dir / md_file
        dst.write_text(content)
        print(f"  生成 agent 指令: {dst}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="hobee",
        description="hobee — 基于 OpenClaw 的兴趣内容策展系统",
    )
    sub = parser.add_subparsers(dest="command")

    # demo
    sub.add_parser("demo", help="零配置体验：订阅示例播客 → 采集 → 展示")

    # status
    sub.add_parser("status", help="显示各兴趣源采集/分析/待分享状态")

    # podcast
    p_podcast = sub.add_parser("podcast", help="播客管理")
    podcast_sub = p_podcast.add_subparsers(dest="podcast_cmd")
    podcast_sub.add_parser("daemon", help="启动播客采集守护进程")
    p_psub = podcast_sub.add_parser("subscribe", help="订阅播客 RSS")
    p_psub.add_argument("url", help="RSS feed URL")
    p_psub.add_argument("--name", default="", help="播客名称")
    p_search = podcast_sub.add_parser("search", help="搜索播客（iTunes）")
    p_search.add_argument("query", help="搜索关键词")
    p_search.add_argument("--limit", type=int, default=10)
    podcast_sub.add_parser("list", help="列出已订阅播客")

    # triage
    p_triage = sub.add_parser("triage", help="运行 LLM 内容分析")
    p_triage.add_argument("hobby", choices=["podcast", "youtube", "twitter"])
    p_triage.add_argument("--batch-size", type=int, default=5)
    p_triage.add_argument("--dry-run", action="store_true")

    # setup
    sub.add_parser("setup", help="交互式配置向导")

    args = parser.parse_args()

    if args.command == "demo":
        cmd_demo(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "podcast":
        if not args.podcast_cmd:
            p_podcast.print_help()
        else:
            cmd_podcast(args)
    elif args.command == "triage":
        cmd_triage(args)
    elif args.command == "setup":
        cmd_setup(args)
    else:
        parser.print_help()
        print("\n  快速开始: hobee demo")


if __name__ == "__main__":
    main()
