#!/usr/bin/env python3
"""Podcast Hobby Daemon — 监控 RSS 订阅、转录新节目、存入后端。

Usage:
    # 守护进程模式（持续运行）
    python daemon.py daemon

    # 管理订阅
    python daemon.py subscribe <rss_url> [name]
    python daemon.py unsubscribe <name>
    python daemon.py list-subscriptions

    # 搜索播客
    python daemon.py search <keyword> [--subscribe N]

    # 手动处理单个音频
    python daemon.py process <audio_url> [--title "..."]

    # 创建分析字段（飞书 Bitable）
    python daemon.py setup-fields
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

# Add project root to path for hobee imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from hobee.config import HobbyConfig
from hobee.daemon import BaseDaemon
from hobee.logging_utils import setup_logging
from podcast_api import parse_rss, format_duration, search_itunes

log = setup_logging("podcast-hobby")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CYCLE_MIN = 120 * 60   # 2 hours
CYCLE_MAX = 240 * 60   # 4 hours
MAX_EPISODES_PER_FEED = 3
WHISPER_TIMEOUT = 1800  # 30 minutes
SEEN_RETENTION_DAYS = 90


# ---------------------------------------------------------------------------
# Whisper transcription
# ---------------------------------------------------------------------------

def transcribe_audio(audio_url, config):
    """Transcribe audio via Whisper API — sends URL, server downloads directly."""
    endpoint = config.get("whisper_endpoint")
    token = config.get("whisper_token", "")

    if not endpoint:
        log.warning("No whisper_endpoint configured, skipping transcription")
        return None

    log.info("Sending audio URL to Whisper API: %s", audio_url)
    try:
        r = requests.post(
            endpoint.rstrip("/") + "/transcribe_url",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"url": audio_url},
            timeout=WHISPER_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        log.info(
            "Transcription complete, got %d segments (download: %.1fs, transcribe: %.1fs)",
            len(data.get("segments", [])),
            data.get("download_time", 0),
            data.get("processing_time", 0),
        )
        return data
    except requests.exceptions.Timeout:
        log.error("Whisper API timed out after %ds", WHISPER_TIMEOUT)
        return None
    except Exception as e:
        log.error("Whisper API call failed: %s", e)
        return None


def whisper_to_markdown(whisper_data, title, podcast_name="", date_str="", duration=""):
    """Convert Whisper API response to markdown transcript."""
    lines = [f"# {title}", ""]

    meta_parts = []
    if podcast_name:
        meta_parts.append(f"**Podcast:** {podcast_name}")
    if date_str:
        meta_parts.append(f"**Date:** {date_str}")
    if duration:
        meta_parts.append(f"**Duration:** {duration}")
    if meta_parts:
        lines.append(" | ".join(meta_parts))
        lines.append("")

    lines.append("---")
    lines.append("")

    segments = whisper_data.get("segments", [])
    if segments:
        for seg in segments:
            start = seg.get("start", 0)
            text = seg.get("text", "").strip()
            if not text:
                continue
            ts = _format_timestamp(start)
            lines.append(f"[{ts}] {text}")
            lines.append("")
    elif whisper_data.get("text"):
        lines.append(whisper_data["text"])
        lines.append("")

    return "\n".join(lines)


def _format_timestamp(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# PodcastDaemon
# ---------------------------------------------------------------------------

class PodcastDaemon(BaseDaemon):
    """播客采集守护进程。"""

    CYCLE_MIN = CYCLE_MIN
    CYCLE_MAX = CYCLE_MAX

    def __init__(self, config, storage):
        super().__init__("podcast", config, storage)
        self.subs_file = self.workspace / "subscriptions.json"
        self.seen_file = self.workspace / "seen-episodes.json"
        self.transcripts_dir = self.workspace / "transcripts"
        self.transcripts_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir = Path("/tmp/podcast-hobby")
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def collect_once(self):
        """Check all subscribed feeds for new episodes."""
        cycle_id = str(uuid.uuid4())[:8]
        subs = self.load_json(self.subs_file, [])
        if not subs:
            log.info("No subscriptions, nothing to check")
            self.log_event("cycle_end", cycle_id=cycle_id, feeds_count=0)
            return

        self.log_event("cycle_start", cycle_id=cycle_id, feeds_count=len(subs))
        seen = self.load_json(self.seen_file, {})
        seen = self._prune_seen(seen)

        for sub in subs:
            name = sub["name"]
            rss_url = sub["rss_url"]
            log.info("Checking feed: %s (%s)", name, rss_url)

            try:
                episodes = parse_rss(rss_url)
            except Exception as e:
                log.error("Failed to parse %s: %s", rss_url, e)
                continue

            new_count = 0
            for ep in episodes:
                if new_count >= MAX_EPISODES_PER_FEED:
                    break
                if ep["guid"] in seen:
                    continue

                seen[ep["guid"]] = time.time()
                new_count += 1

                try:
                    self._process_episode(ep, name)
                except Exception as e:
                    log.error("Error processing %s: %s", ep["guid"], e, exc_info=True)

                self.save_json(self.seen_file, seen)

            if new_count:
                log.info("Processed %d new episodes from %s", new_count, name)
                self.log_event("feed_processed", cycle_id=cycle_id, podcast=name, count=new_count)

        self.save_json(self.seen_file, seen)
        pending_count = len(self.load_pending())
        self.log_event("cycle_end", cycle_id=cycle_id, feeds_count=len(subs), pending=pending_count)

    def _process_episode(self, episode, podcast_name):
        """Process a single new episode end-to-end."""
        guid = episode["guid"]
        title = episode["title"]
        audio_url = episode["audio_url"]
        duration = format_duration(episode.get("duration", 0))
        pub = episode.get("published")
        date_str = ""
        if pub:
            try:
                date_str = time.strftime("%Y-%m-%d", pub)
            except Exception:
                date_str = ""

        log.info("Processing: [%s] %s", podcast_name, title)

        md_path = None
        try:
            # 1. Create storage record (with dedup)
            record_id = self.dedup_and_store(guid, {
                "编号": guid[:200],
                "名称": title,
                "播客名称": podcast_name,
                "原始音频": {"link": audio_url, "text": title},
            })

            # 2. Transcribe via Whisper API
            whisper_data = transcribe_audio(audio_url, self.config)
            if not whisper_data:
                log.warning("Transcription skipped (not configured or failed)")
                # Still add to pending without transcript
                self._add_to_pending(guid, title, podcast_name, audio_url, date_str, duration, record_id, "")
                return

            # 3. Generate markdown transcript
            md_content = whisper_to_markdown(whisper_data, title, podcast_name, date_str, duration)
            safe_name = re.sub(r"[^\w\-.]", "_", guid)[:100]
            md_path = self.tmp_dir / f"{safe_name}.md"
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_content)

            # 4. Upload transcript to storage
            try:
                transcript_name = f"{title[:50].strip()}.md"
                file_token = self.storage.upload_media(str(md_path), transcript_name)
                now_ms = int(time.time()) * 1000
                self.storage.update_record(record_id, {
                    "转录文件": [{"file_token": file_token}],
                    "转录时间": now_ms,
                })
            except Exception as e:
                log.warning("Failed to upload transcript: %s", e)

            # 5. Save transcript locally
            persistent_path = self.transcripts_dir / f"{safe_name}.txt"
            persistent_path.write_text(md_content, encoding="utf-8")

            # 6. Add to pending shares
            self._add_to_pending(
                guid, title, podcast_name, audio_url, date_str, duration,
                record_id, md_content, str(persistent_path),
            )

            log.info("Successfully processed: %s", title)

        finally:
            if md_path and md_path.exists():
                md_path.unlink()

    def _add_to_pending(self, guid, title, podcast_name, audio_url, date_str, duration,
                        record_id, md_content="", transcript_path=""):
        safe_name = re.sub(r"[^\w\-.]", "_", guid)[:100]
        summary = md_content[:1500] if md_content else f"[{podcast_name}] {title}"
        self.add_pending_item({
            "id": f"podcast-{safe_name}",
            "source": "podcast",
            "title": title,
            "podcast": podcast_name,
            "audio_url": audio_url,
            "transcript_path": transcript_path,
            "transcript_preview": md_content[:500] if md_content else "",
            "summary": summary,
            "record_id": record_id,
            "date": date_str,
            "duration": duration,
            "shared": False,
            "triaged": False,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        })

    @staticmethod
    def _prune_seen(seen):
        cutoff = time.time() - SEEN_RETENTION_DAYS * 86400
        return {k: v for k, v in seen.items() if v > cutoff}


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_subscribe(args, config):
    subs_file = config.workspace / "subscriptions.json"
    subs = BaseDaemon.load_json(subs_file, [])

    for s in subs:
        if s["rss_url"] == args.rss_url:
            print(f"Already subscribed: {s['name']} ({args.rss_url})")
            return

    name = args.name
    if not name:
        try:
            import feedparser
            feed = feedparser.parse(args.rss_url)
            name = feed.feed.get("title", args.rss_url)
        except Exception:
            name = args.rss_url

    subs.append({
        "name": name,
        "rss_url": args.rss_url,
        "added_at": datetime.now(timezone.utc).isoformat(),
    })
    BaseDaemon.save_json(subs_file, subs)
    print(f"Subscribed to: {name}")
    print(f"  RSS: {args.rss_url}")


def cmd_unsubscribe(args, config):
    subs_file = config.workspace / "subscriptions.json"
    subs = BaseDaemon.load_json(subs_file, [])
    before = len(subs)
    subs = [s for s in subs if s["name"].lower() != args.name.lower()]
    if len(subs) == before:
        print(f"Not found: {args.name}")
        return
    BaseDaemon.save_json(subs_file, subs)
    print(f"Unsubscribed from: {args.name}")


def cmd_list(config):
    subs_file = config.workspace / "subscriptions.json"
    subs = BaseDaemon.load_json(subs_file, [])
    if not subs:
        print("No subscriptions.")
        return
    for i, s in enumerate(subs, 1):
        print(f"  {i}. {s['name']}")
        print(f"     RSS: {s['rss_url']}")


def cmd_search(args):
    results = search_itunes(args.query, max_results=args.limit)
    if not results:
        print(f"No podcasts found for: {args.query}")
        return
    print(f"Found {len(results)} podcasts for \"{args.query}\":\n")
    for i, r in enumerate(results, 1):
        print(f"  {i}. {r['title']}")
        if r["author"]:
            print(f"     Author: {r['author']}")
        if r["genre"]:
            print(f"     Genre: {r['genre']}  Episodes: {r['episode_count']}")
        print(f"     RSS: {r['rss_url']}")
        print()


def cmd_setup_fields(config):
    storage = config.create_storage()
    existing = {f["field_name"] for f in storage.list_fields()}
    for name in ["摘要", "亮点", "精选原文", "主题标签"]:
        if name in existing:
            print(f"  Field '{name}' already exists, skipping")
        else:
            field_id = storage.create_field(name, field_type=1)
            print(f"  Created field '{name}' -> {field_id}")
    print("Done. Fields ready for LLM triage.")


def cmd_daemon(config):
    storage = config.create_storage()
    daemon = PodcastDaemon(config, storage)
    daemon.run_forever()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    config = HobbyConfig("podcast")
    config.workspace.mkdir(parents=True, exist_ok=True)

    parser = argparse.ArgumentParser(description="Podcast Hobby Daemon")
    sub = parser.add_subparsers(dest="command")

    p_sub = sub.add_parser("subscribe", help="Subscribe to a podcast RSS feed")
    p_sub.add_argument("rss_url", help="RSS feed URL")
    p_sub.add_argument("name", nargs="?", default="", help="Podcast name")

    p_unsub = sub.add_parser("unsubscribe", help="Unsubscribe from a podcast")
    p_unsub.add_argument("name", help="Podcast name")

    sub.add_parser("list-subscriptions", help="List all subscriptions")

    p_search = sub.add_parser("search", help="Search podcasts via iTunes")
    p_search.add_argument("query", help="Search keyword(s)")
    p_search.add_argument("--limit", type=int, default=10)

    sub.add_parser("daemon", help="Run the daemon loop")
    sub.add_parser("setup-fields", help="Create analysis columns in storage backend")

    args = parser.parse_args()

    if args.command == "subscribe":
        cmd_subscribe(args, config)
    elif args.command == "unsubscribe":
        cmd_unsubscribe(args, config)
    elif args.command == "list-subscriptions":
        cmd_list(config)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "daemon":
        cmd_daemon(config)
    elif args.command == "setup-fields":
        cmd_setup_fields(config)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
