"""Podcast search (iTunes + Podcast Index) and RSS feed parser.

iTunes Search API 是免费的，无需 API Key。
Podcast Index API 是可选的（需要注册获取 Key + Secret）。
"""

import hashlib
import time
import os
import logging
import requests
import feedparser

log = logging.getLogger(__name__)

PODCAST_INDEX_BASE = "https://api.podcastindex.org/api/1.0"
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"


def search_itunes(query, max_results=10):
    """Search podcasts via Apple iTunes Search API (free, no key needed).

    Returns list of dicts with: title, author, rss_url, artwork, description, genre.
    """
    r = requests.get(
        ITUNES_SEARCH_URL,
        params={
            "term": query,
            "media": "podcast",
            "limit": max_results,
        },
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()

    results = []
    for item in data.get("results", []):
        rss_url = item.get("feedUrl")
        if not rss_url:
            continue
        results.append({
            "title": item.get("collectionName", "Unknown"),
            "author": item.get("artistName", ""),
            "rss_url": rss_url,
            "artwork": item.get("artworkUrl100", ""),
            "description": item.get("collectionName", ""),
            "genre": item.get("primaryGenreName", ""),
            "episode_count": item.get("trackCount", 0),
        })
    return results


class PodcastIndexAPI:
    """Client for Podcast Index API (optional, requires API key + secret).

    注册获取 Key/Secret: https://api.podcastindex.org
    """

    def __init__(self, api_key=None, api_secret=None):
        self.api_key = api_key or os.environ.get("PODCAST_INDEX_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("PODCAST_INDEX_API_SECRET", "")
        if not self.api_key or not self.api_secret:
            raise ValueError(
                "Podcast Index API requires both PODCAST_INDEX_API_KEY and "
                "PODCAST_INDEX_API_SECRET environment variables."
            )

    def _headers(self):
        ts = str(int(time.time()))
        auth_hash = hashlib.sha1(
            (self.api_key + self.api_secret + ts).encode()
        ).hexdigest()
        return {
            "X-Auth-Key": self.api_key,
            "X-Auth-Date": ts,
            "Authorization": auth_hash,
            "User-Agent": "OpenClaw-Hobby/1.0",
        }

    def _get(self, path, params=None):
        r = requests.get(
            f"{PODCAST_INDEX_BASE}{path}",
            headers=self._headers(),
            params=params or {},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def search(self, query, max_results=10):
        """Search for podcasts by name."""
        data = self._get("/search/byterm", {"q": query, "max": max_results})
        return data.get("feeds", [])

    def get_podcast_info(self, feed_id):
        """Get podcast details by feed ID."""
        data = self._get("/podcasts/byfeedid", {"id": feed_id})
        return data.get("feed", {})

    def get_episodes(self, feed_id, since=None, max_results=20):
        """Get recent episodes for a podcast."""
        params = {"id": feed_id, "max": max_results}
        if since:
            params["since"] = int(since)
        data = self._get("/episodes/byfeedid", params)
        return data.get("items", [])


def parse_rss(feed_url, timeout=30):
    """Parse RSS feed and return episodes.

    Returns list of dicts with: guid, title, published, audio_url, duration, description
    """
    log.info("Parsing RSS: %s", feed_url)
    try:
        resp = requests.get(feed_url, timeout=timeout)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except requests.RequestException as e:
        log.error("RSS fetch failed for %s: %s", feed_url, e)
        return []

    if feed.bozo and not feed.entries:
        log.error("RSS parse error for %s: %s", feed_url, feed.bozo_exception)
        return []

    episodes = []
    for entry in feed.entries:
        # Find audio enclosure
        audio_url = None
        for link in getattr(entry, "enclosures", []):
            if link.get("type", "").startswith("audio/") or link.get("href", "").endswith((".mp3", ".m4a", ".wav")):
                audio_url = link.get("href")
                break

        if not audio_url:
            for mc in getattr(entry, "media_content", []):
                if mc.get("type", "").startswith("audio/"):
                    audio_url = mc.get("url")
                    break

        if not audio_url:
            continue

        raw_dur = getattr(entry, "itunes_duration", None) or ""
        duration = _parse_duration(raw_dur)

        episodes.append({
            "guid": entry.get("id") or entry.get("link") or audio_url,
            "title": entry.get("title", "Untitled"),
            "published": getattr(entry, "published_parsed", None),
            "audio_url": audio_url,
            "duration": duration,
            "description": entry.get("summary", ""),
        })

    log.info("Found %d episodes with audio in %s", len(episodes), feed_url)
    return episodes


def _parse_duration(raw):
    """Parse duration string to seconds."""
    if not raw:
        return 0
    try:
        if ":" in raw:
            parts = raw.split(":")
            parts = [int(p) for p in parts]
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            elif len(parts) == 2:
                return parts[0] * 60 + parts[1]
        return int(raw)
    except (ValueError, TypeError):
        return 0


def format_duration(seconds):
    """Format seconds as HH:MM:SS or MM:SS."""
    if not seconds:
        return "unknown"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
