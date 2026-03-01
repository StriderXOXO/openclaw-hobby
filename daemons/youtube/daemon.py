#!/usr/bin/env python3
"""YouTube Hobby Daemon — 监控订阅频道、提取字幕、存入后端。

运行模式：
    # 守护进程
    python daemon.py daemon

    # 单次采集
    python daemon.py once

    # 创建分析字段
    python daemon.py setup-fields

采集策略（加权随机选择）：
- check_subscriptions (65%): 检查订阅频道的最新视频
- deep_dive (25%): 深入查看某个频道的更多视频
- topic_discovery (10%): 按兴趣话题搜索（配额消耗大，慎用）
"""

import argparse
import json
import math
import os
import random
import re
import sys
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from hobee.config import HobbyConfig
from hobee.daemon import BaseDaemon
from hobee.logging_utils import setup_logging
from youtube_api import YouTubeAPI, YouTubeAPIError

log = setup_logging("youtube-hobby")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CYCLE_MIN = 4 * 3600    # 4 hours
CYCLE_MAX = 6 * 3600    # 6 hours

SUBSCRIPTIONS_CACHE_TTL = 24 * 3600  # 24 hours
SEEN_VIDEOS_MAX_AGE = 30 * 24 * 3600  # 30 days

SUBTITLE_LANGS = "zh-Hans,zh,en"
SUBTITLE_MAX_CHARS = 50000
SUBTITLE_PREVIEW_CHARS = 500

DEFAULT_STRATEGIES = {
    "check_subscriptions": 65,
    "deep_dive": 25,
    "topic_discovery": 10,
}


# ---------------------------------------------------------------------------
# Subtitle extraction
# ---------------------------------------------------------------------------

def strip_subtitle_formatting(text):
    """Remove VTT/SRT timestamps, formatting tags, and deduplicate lines."""
    text = re.sub(r"^WEBVTT\n.*?\n\n", "", text, flags=re.DOTALL)
    text = re.sub(r"\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[.,]\d{3}.*\n?", "", text)
    text = re.sub(r"^\d+\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"^(align|position|line|size):.*$", "", text, flags=re.MULTILINE | re.IGNORECASE)
    lines = text.strip().split("\n")
    deduped = []
    prev = ""
    for line in lines:
        line = line.strip()
        if line and line != prev:
            deduped.append(line)
            prev = line
    return "\n".join(deduped)


def extract_subtitles_via_proxy(video_id, config):
    """Extract subtitles via transcript proxy (bypasses YouTube IP blocks on cloud providers).

    If your VPS IP is blocked by YouTube, route transcript requests through a machine
    with a residential IP. Configure YOUTUBE_TRANSCRIPT_PROXY_URL and YOUTUBE_TRANSCRIPT_PROXY_TOKEN.

    Returns (clean_text, vtt_content) or (None, None).
    """
    proxy_url = config.get("youtube_transcript_proxy_url") or config.get("transcript_proxy_url")
    proxy_token = config.get("youtube_transcript_proxy_token") or config.get("transcript_proxy_token")

    if not proxy_url:
        log.info("No transcript proxy configured, skipping proxy extraction")
        return None, None

    lang_prefs = ["zh-Hans", "zh", "zh-Hant", "en"]
    try:
        resp = requests.post(
            proxy_url,
            headers={
                "Authorization": f"Bearer {proxy_token}" if proxy_token else "",
                "Content-Type": "application/json",
            },
            json={"video_id": video_id, "languages": lang_prefs},
            timeout=120,
        )
        if resp.status_code == 404:
            log.info("No transcript available for %s (proxy 404)", video_id)
            return None, None
        if resp.status_code != 200:
            log.warning("Transcript proxy failed for %s: %d", video_id, resp.status_code)
            return None, None

        data = resp.json()
        clean_text = data.get("text", "")
        snippets = data.get("snippets", [])

        if not clean_text or not snippets:
            return None, None

        log.info("Got transcript for %s via proxy (lang: %s)", video_id, data.get("language", "?"))

        # Build VTT content
        vtt_lines = ["WEBVTT", ""]
        for i, snippet in enumerate(snippets, 1):
            start = snippet.get("start", 0)
            dur = snippet.get("duration", 3)
            end = start + dur
            text = snippet.get("text", "")
            vtt_lines.append(str(i))
            vtt_lines.append(f"{_fmt_vtt(start)} --> {_fmt_vtt(end)}")
            vtt_lines.append(text)
            vtt_lines.append("")

        return clean_text, "\n".join(vtt_lines)

    except Exception as e:
        log.warning("Transcript proxy error for %s: %s", video_id, e)
        return None, None


def extract_subtitles_via_ytdlp(video_id):
    """Extract subtitles via yt-dlp (works if YouTube doesn't block your IP).

    Returns (clean_text, vtt_content) or (None, None).
    """
    import subprocess
    import tempfile

    url = f"https://www.youtube.com/watch?v={video_id}"
    with tempfile.TemporaryDirectory(prefix="yt-sub-") as tmpdir:
        cmd = [
            "yt-dlp", "--skip-download",
            "--write-subs", "--write-auto-subs",
            "--sub-langs", SUBTITLE_LANGS,
            "--sub-format", "vtt",
            "-o", f"{tmpdir}/%(id)s.%(ext)s",
            url,
        ]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            log.warning("yt-dlp failed for %s: %s", video_id, e)
            return None, None

        # Find the best subtitle file
        from pathlib import Path
        vtt_files = list(Path(tmpdir).glob(f"{video_id}*.vtt"))
        if not vtt_files:
            return None, None

        vtt_path = vtt_files[0]
        vtt_content = vtt_path.read_text(encoding="utf-8", errors="replace")
        clean_text = strip_subtitle_formatting(vtt_content)

        if len(clean_text) < 50:
            return None, None

        return clean_text, vtt_content


def _fmt_vtt(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


# ---------------------------------------------------------------------------
# YouTubeDaemon
# ---------------------------------------------------------------------------

class YouTubeDaemon(BaseDaemon):
    """YouTube 采集守护进程。"""

    CYCLE_MIN = CYCLE_MIN
    CYCLE_MAX = CYCLE_MAX

    def __init__(self, config, storage):
        super().__init__("youtube", config, storage)
        self.token_file = self.workspace / "token.json"
        self.seen_file = self.workspace / "seen-videos.json"
        self.subs_cache_file = self.workspace / "subscriptions-cache.json"
        self.interests_file = self.workspace / "interests.json"
        self.subtitles_dir = self.workspace / "subtitles"
        self.subtitles_dir.mkdir(parents=True, exist_ok=True)
        self.api = YouTubeAPI(str(self.token_file))

    def collect_once(self):
        """Run one collection cycle."""
        strategy = self._pick_strategy()
        log.info("Strategy: %s", strategy)
        self.log_event("strategy_selected", strategy=strategy)

        if strategy == "check_subscriptions":
            self._check_subscriptions()
        elif strategy == "deep_dive":
            self._deep_dive()
        elif strategy == "topic_discovery":
            self._topic_discovery()

    def _pick_strategy(self):
        choices = list(DEFAULT_STRATEGIES.keys())
        weights = list(DEFAULT_STRATEGIES.values())
        return random.choices(choices, weights=weights, k=1)[0]

    def _check_subscriptions(self):
        """Check subscribed channels for new uploads."""
        subs = self._get_cached_subscriptions()
        if not subs:
            log.info("No subscriptions found")
            return

        # Get upload playlist IDs
        channel_ids = [s["channelId"] for s in subs]
        playlists = self.api.get_channel_upload_playlists(channel_ids)

        seen = self._load_seen()
        new_videos = []

        # Check each channel (randomize order, limit checks per cycle)
        random.shuffle(subs)
        channels_checked = 0
        max_channels = min(len(subs), 15)

        for sub in subs[:max_channels]:
            cid = sub["channelId"]
            playlist_id = playlists.get(cid)
            if not playlist_id:
                continue

            try:
                video_ids = self.api.get_recent_uploads(playlist_id, max_results=5)
            except YouTubeAPIError as e:
                log.warning("Failed to get uploads for %s: %s", sub["title"], e)
                continue

            unseen = [vid for vid in video_ids if vid not in seen["videos"]]
            if unseen:
                new_videos.extend(unseen)
                log.info("%s: %d new videos", sub["title"], len(unseen))

            channels_checked += 1

        if not new_videos:
            log.info("No new videos found across %d channels", channels_checked)
            return

        # Get details and process
        details = self.api.get_video_details(new_videos[:20])
        for video in details:
            self._process_video(video)
            self._mark_seen(seen, video["videoId"])

        self._save_seen(seen)
        log.info("Processed %d new videos", len(details))

    def _deep_dive(self):
        """Deep dive into a random subscribed channel."""
        subs = self._get_cached_subscriptions()
        if not subs:
            return

        channel = random.choice(subs)
        log.info("Deep diving: %s", channel["title"])

        playlists = self.api.get_channel_upload_playlists([channel["channelId"]])
        playlist_id = playlists.get(channel["channelId"])
        if not playlist_id:
            return

        video_ids = self.api.get_recent_uploads(playlist_id, max_results=10)
        seen = self._load_seen()
        unseen = [vid for vid in video_ids if vid not in seen["videos"]]

        if unseen:
            details = self.api.get_video_details(unseen[:5])
            for video in details:
                self._process_video(video)
                self._mark_seen(seen, video["videoId"])
            self._save_seen(seen)

    def _topic_discovery(self):
        """Search for videos by interest topics (expensive — 100 quota units)."""
        interests = self.load_json(self.interests_file, {"topics": {}})
        topics = list(interests.get("topics", {}).keys())
        if not topics:
            log.info("No topics configured, skipping discovery")
            return

        topic = random.choice(topics)
        log.info("Topic discovery: %s", topic)

        video_ids = self.api.search_videos(topic, max_results=5)
        if video_ids:
            details = self.api.get_video_details(video_ids)
            seen = self._load_seen()
            for video in details:
                if video["videoId"] not in seen["videos"]:
                    self._process_video(video)
                    self._mark_seen(seen, video["videoId"])
            self._save_seen(seen)

    def _process_video(self, video):
        """Process a single video: extract subtitles, store, add to pending."""
        video_id = video["videoId"]
        title = video["title"]
        channel = video.get("channelTitle", "")

        log.info("Processing: [%s] %s", channel, title)

        # Store in backend
        record_id = self.dedup_and_store(video_id, {
            "编号": video_id,
            "名称": title,
            "频道": channel,
            "链接": {"link": f"https://youtube.com/watch?v={video_id}", "text": title},
            "发布时间": video.get("publishedAt", ""),
        })

        # Extract subtitles (try proxy first, fallback to yt-dlp)
        clean_text, vtt_content = extract_subtitles_via_proxy(video_id, self.config)
        if not clean_text:
            clean_text, vtt_content = extract_subtitles_via_ytdlp(video_id)

        subtitles_path = ""
        if clean_text:
            # Truncate if too long
            if len(clean_text) > SUBTITLE_MAX_CHARS:
                clean_text = clean_text[:SUBTITLE_MAX_CHARS] + "\n\n[... truncated]"

            # Save locally
            sub_file = self.subtitles_dir / f"{video_id}.txt"
            sub_file.write_text(clean_text, encoding="utf-8")
            subtitles_path = str(sub_file)

            # Upload VTT to storage
            if vtt_content:
                try:
                    vtt_file = self.subtitles_dir / f"{video_id}.vtt"
                    vtt_file.write_text(vtt_content, encoding="utf-8")
                    file_token = self.storage.upload_media(str(vtt_file), f"{title[:50]}.vtt")
                    self.storage.update_record(record_id, {
                        "字幕文件": [{"file_token": file_token}],
                    })
                except Exception as e:
                    log.warning("Failed to upload subtitles: %s", e)

        # Add to pending shares
        summary = clean_text[:1500] if clean_text else f"[{channel}] {title} - {video.get('description', '')[:500]}"
        self.add_pending_item({
            "id": f"youtube-{video_id}",
            "source": "youtube",
            "video_id": video_id,
            "title": title,
            "channel": channel,
            "url": f"https://youtube.com/watch?v={video_id}",
            "subtitles_path": subtitles_path,
            "subtitles_preview": clean_text[:SUBTITLE_PREVIEW_CHARS] if clean_text else "",
            "summary": summary,
            "record_id": record_id,
            "published_at": video.get("publishedAt", ""),
            "views": video.get("viewCount", 0),
            "likes": video.get("likeCount", 0),
            "duration": video.get("duration", ""),
            "shared": False,
            "triaged": False,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        })

    # --- State helpers ---

    def _get_cached_subscriptions(self):
        cache = self.load_json(self.subs_cache_file, {})
        now = time.time()
        if cache.get("data") and now - cache.get("fetched_at", 0) < SUBSCRIPTIONS_CACHE_TTL:
            log.info("Using cached subscriptions (%d channels)", len(cache["data"]))
            return cache["data"]

        log.info("Refreshing subscriptions cache")
        subs = self.api.get_subscriptions(max_results=200)
        self.save_json(self.subs_cache_file, {"data": subs, "fetched_at": now})
        log.info("Cached %d subscriptions", len(subs))
        return subs

    def _load_seen(self):
        return self.load_json(self.seen_file, {"videos": {}, "last_cleanup": ""})

    def _mark_seen(self, seen, video_id):
        seen["videos"][video_id] = datetime.now(timezone.utc).isoformat()

    def _save_seen(self, seen):
        # Periodic cleanup
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if not seen.get("last_cleanup", "").startswith(today):
            cutoff = (datetime.now(timezone.utc) - timedelta(seconds=SEEN_VIDEOS_MAX_AGE)).isoformat()
            seen["videos"] = {vid: ts for vid, ts in seen["videos"].items() if ts > cutoff}
            seen["last_cleanup"] = datetime.now(timezone.utc).isoformat()
        self.save_json(self.seen_file, seen)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_daemon(config):
    storage = config.create_storage()
    daemon = YouTubeDaemon(config, storage)
    daemon.run_forever()


def cmd_once(config):
    storage = config.create_storage()
    daemon = YouTubeDaemon(config, storage)
    daemon.collect_once()


def cmd_setup_fields(config):
    storage = config.create_storage()
    existing = {f["field_name"] for f in storage.list_fields()}
    for name in ["摘要", "亮点", "精选原文", "主题标签"]:
        if name in existing:
            print(f"  Field '{name}' already exists")
        else:
            field_id = storage.create_field(name, field_type=1)
            print(f"  Created '{name}' -> {field_id}")
    print("Done.")


def main():
    config = HobbyConfig("youtube")
    config.workspace.mkdir(parents=True, exist_ok=True)

    parser = argparse.ArgumentParser(description="YouTube Hobby Daemon")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("daemon", help="Run the daemon loop")
    sub.add_parser("once", help="Run one collection cycle")
    sub.add_parser("setup-fields", help="Create analysis columns")

    args = parser.parse_args()

    if args.command == "daemon":
        cmd_daemon(config)
    elif args.command == "once":
        cmd_once(config)
    elif args.command == "setup-fields":
        cmd_setup_fields(config)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
