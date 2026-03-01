#!/usr/bin/env python3
"""Twitter Hobby Daemon — 像好奇的人类一样浏览 Twitter，发现有趣的人和内容。

运行模式：
    # 守护进程
    python daemon.py daemon

    # 单次采集
    python daemon.py once

    # 创建分析字段
    python daemon.py setup-fields

采集策略（加权随机选择）：
- check_timeline (40%): 检查关注用户的最新推文
- explore_people (25%): 根据兴趣发现新用户
- topic_search (20%): 按话题搜索推文
- thread_reading (15%): 深入阅读某个有趣的线程
"""

import argparse
import json
import math
import os
import random
import sys
import time
import logging
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from hobee.config import HobbyConfig
from hobee.daemon import BaseDaemon
from hobee.logging_utils import setup_logging
from twitter_api import TwitterAPI, TwitterAPIError

log = setup_logging("twitter-hobby")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_TWEET_AGE_HOURS = 72
SEEN_TWEETS_RETENTION_DAYS = 30
CYCLE_MIN = 90 * 60     # 1.5 hours
CYCLE_MAX = 180 * 60    # 3 hours

MAX_FOLLOWS_PER_DAY = 3
FOLLOW_SIGHTINGS_REQUIRED = 3

DEFAULT_STRATEGIES = {
    "check_timeline": 40,
    "explore_people": 25,
    "topic_search": 20,
    "thread_reading": 15,
}


# ---------------------------------------------------------------------------
# Tweet utilities
# ---------------------------------------------------------------------------

def extract_tweets(result):
    """Extract tweet list from API response (handles varying shapes)."""
    if not result:
        return []
    data = result.get("data")
    if isinstance(data, dict):
        tweets = data.get("tweets")
        if isinstance(tweets, list):
            return tweets
    tweets = result.get("tweets")
    if isinstance(tweets, list):
        return tweets
    return []


def parse_tweet_time(tweet):
    """Parse tweet createdAt field into a datetime."""
    created = tweet.get("createdAt")
    if not created:
        return None
    try:
        return parsedate_to_datetime(created)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(created.replace("Z", "+00:00"))
    except Exception:
        return None


def is_tweet_fresh(tweet, max_age_hours=MAX_TWEET_AGE_HOURS):
    """Check if a tweet is within the recency window."""
    dt = parse_tweet_time(tweet)
    if dt is None:
        return True
    age = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    return age <= max_age_hours


def tweet_age_hours(tweet):
    """Return tweet age in hours."""
    dt = parse_tweet_time(tweet)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


def engagement_score_with_decay(tweet, half_life_hours=24):
    """Compute engagement score with exponential time decay."""
    likes = tweet.get("likeCount") or 0
    replies = tweet.get("replyCount") or 0
    retweets = tweet.get("retweetCount") or 0
    raw = likes + replies * 3 + retweets * 2
    age = tweet_age_hours(tweet)
    if age is None:
        return raw
    decay = math.exp(-0.693 * age / half_life_hours)
    return raw * decay


def format_tweet_item(tweet, source_strategy=""):
    """Format a tweet into a pending share item."""
    author = tweet.get("author", {})
    username = author.get("userName", "") or tweet.get("user", {}).get("screen_name", "")
    text = tweet.get("text", "")
    tweet_id = tweet.get("id", "") or tweet.get("tweetId", "")

    return {
        "id": f"twitter-{tweet_id}",
        "source": "twitter",
        "tweet_id": str(tweet_id),
        "user": username,
        "text": text,
        "url": f"https://x.com/{username}/status/{tweet_id}" if username and tweet_id else "",
        "summary": f"@{username}: {text[:500]}",
        "engagement": {
            "likes": tweet.get("likeCount", 0),
            "replies": tweet.get("replyCount", 0),
            "retweets": tweet.get("retweetCount", 0),
        },
        "score": engagement_score_with_decay(tweet),
        "strategy": source_strategy,
        "shared": False,
        "triaged": False,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# TwitterDaemon
# ---------------------------------------------------------------------------

class TwitterDaemon(BaseDaemon):
    """Twitter 采集守护进程。"""

    CYCLE_MIN = CYCLE_MIN
    CYCLE_MAX = CYCLE_MAX

    def __init__(self, config, storage):
        super().__init__("twitter", config, storage)
        self.following_file = self.workspace / "following.json"
        self.interests_file = self.workspace / "interests.json"
        self.seen_file = self.workspace / "seen-tweets.json"

        api_key = config.require("twitter_api_key")
        self.api = TwitterAPI(api_key)

    def collect_once(self):
        """Run one browse session."""
        strategy = self._pick_strategy()
        log.info("Strategy: %s", strategy)
        self.log_event("strategy_selected", strategy=strategy)

        seen = self._load_seen()
        seen = self._prune_seen(seen)

        try:
            if strategy == "check_timeline":
                self._check_timeline(seen)
            elif strategy == "explore_people":
                self._explore_people(seen)
            elif strategy == "topic_search":
                self._topic_search(seen)
            elif strategy == "thread_reading":
                self._thread_reading(seen)
        finally:
            self._save_seen(seen)

    def _pick_strategy(self):
        choices = list(DEFAULT_STRATEGIES.keys())
        weights = list(DEFAULT_STRATEGIES.values())
        return random.choices(choices, weights=weights, k=1)[0]

    def _check_timeline(self, seen):
        """Check recent tweets from followed accounts."""
        following = self.load_json(self.following_file, {"accounts": []})
        accounts = following.get("accounts", [])
        if not accounts:
            log.info("No followed accounts, switching to topic_search")
            self._topic_search(seen)
            return

        random.shuffle(accounts)
        checked = 0
        collected = 0

        for account in accounts[:10]:
            username = account.get("username", "")
            if not username:
                continue

            try:
                result = self.api.get_user_tweets(username=username)
                tweets = extract_tweets(result)
            except TwitterAPIError as e:
                log.warning("Failed to get tweets for @%s: %s", username, e)
                continue

            for tweet in tweets:
                if not is_tweet_fresh(tweet):
                    continue
                tweet_id = str(tweet.get("id", ""))
                if tweet_id in seen:
                    continue

                item = format_tweet_item(tweet, "check_timeline")
                if item["score"] > 5:  # minimum engagement threshold
                    self._store_and_queue(item, seen)
                    collected += 1

                seen[tweet_id] = time.time()

            checked += 1

        log.info("Checked %d accounts, collected %d tweets", checked, collected)
        self.log_event("timeline_checked", accounts=checked, collected=collected)

    def _explore_people(self, seen):
        """Discover new interesting people based on interests."""
        interests = self.load_json(self.interests_file, {"topics": {}, "people": {}})
        topics = list(interests.get("topics", {}).keys())
        if not topics:
            log.info("No topics configured for people discovery")
            return

        topic = random.choice(topics)
        log.info("Exploring people for topic: %s", topic)

        try:
            result = self.api.search_users(topic)
            users = result.get("users", []) if result else []
        except TwitterAPIError as e:
            log.warning("User search failed: %s", e)
            return

        for user in users[:5]:
            username = user.get("userName", "")
            if not username:
                continue

            try:
                result = self.api.get_user_tweets(username=username)
                tweets = extract_tweets(result)
            except TwitterAPIError:
                continue

            for tweet in tweets[:3]:
                if not is_tweet_fresh(tweet):
                    continue
                tweet_id = str(tweet.get("id", ""))
                if tweet_id in seen:
                    continue

                item = format_tweet_item(tweet, "explore_people")
                if item["score"] > 10:
                    self._store_and_queue(item, seen)
                seen[tweet_id] = time.time()

    def _topic_search(self, seen):
        """Search tweets by topic keywords."""
        interests = self.load_json(self.interests_file, {"topics": {}})
        topics = list(interests.get("topics", {}).keys())
        if not topics:
            log.info("No topics configured for search")
            return

        topic = random.choice(topics)
        log.info("Searching tweets for: %s", topic)

        try:
            result = self.api.search_tweets(topic, query_type="Latest")
            tweets = extract_tweets(result)
        except TwitterAPIError as e:
            log.warning("Tweet search failed: %s", e)
            return

        collected = 0
        for tweet in tweets:
            if not is_tweet_fresh(tweet):
                continue
            tweet_id = str(tweet.get("id", ""))
            if tweet_id in seen:
                continue

            item = format_tweet_item(tweet, "topic_search")
            if item["score"] > 15:
                self._store_and_queue(item, seen)
                collected += 1

            seen[tweet_id] = time.time()

        log.info("Topic search '%s': collected %d tweets", topic, collected)

    def _thread_reading(self, seen):
        """Find and read an interesting thread in depth."""
        pending = self.load_pending()
        high_engagement = [
            p for p in pending
            if p.get("source") == "twitter" and p.get("score", 0) > 20
        ]

        if not high_engagement:
            log.info("No high-engagement tweets to thread-read, switching to timeline")
            self._check_timeline(seen)
            return

        target = random.choice(high_engagement)
        tweet_id = target.get("tweet_id")
        if not tweet_id:
            return

        log.info("Reading thread for tweet %s", tweet_id)

        try:
            result = self.api.get_thread(tweet_id)
            tweets = extract_tweets(result)
        except TwitterAPIError as e:
            log.warning("Thread read failed: %s", e)
            return

        for tweet in tweets:
            tid = str(tweet.get("id", ""))
            if tid == tweet_id or tid in seen:
                continue
            item = format_tweet_item(tweet, "thread_reading")
            if item["score"] > 5:
                self._store_and_queue(item, seen)
            seen[tid] = time.time()

    def _store_and_queue(self, item, seen):
        """Store in backend and add to pending."""
        tweet_id = item.get("tweet_id", "")

        # Store in backend
        try:
            record_id = self.dedup_and_store(tweet_id, {
                "编号": tweet_id,
                "用户": item.get("user", ""),
                "内容": item.get("text", "")[:2000],
                "链接": {"link": item.get("url", ""), "text": f"@{item.get('user', '')}"},
            })
            item["record_id"] = record_id
        except Exception as e:
            log.warning("Failed to store tweet %s: %s", tweet_id, e)

        self.add_pending_item(item)

    # --- Seen tweets ---

    def _load_seen(self):
        return self.load_json(self.seen_file, {})

    def _save_seen(self, seen):
        self.save_json(self.seen_file, seen)

    @staticmethod
    def _prune_seen(seen):
        cutoff = time.time() - SEEN_TWEETS_RETENTION_DAYS * 86400
        return {k: v for k, v in seen.items() if isinstance(v, (int, float)) and v > cutoff}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_daemon(config):
    storage = config.create_storage()
    daemon = TwitterDaemon(config, storage)
    daemon.run_forever()


def cmd_once(config):
    storage = config.create_storage()
    daemon = TwitterDaemon(config, storage)
    daemon.collect_once()


def cmd_setup_fields(config):
    storage = config.create_storage()
    existing = {f["field_name"] for f in storage.list_fields()}
    for name in ["摘要", "主题标签"]:
        if name in existing:
            print(f"  Field '{name}' already exists")
        else:
            field_id = storage.create_field(name, field_type=1)
            print(f"  Created '{name}' -> {field_id}")
    print("Done.")


def main():
    config = HobbyConfig("twitter")
    config.workspace.mkdir(parents=True, exist_ok=True)

    parser = argparse.ArgumentParser(description="Twitter Hobby Daemon")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("daemon", help="Run the daemon loop")
    sub.add_parser("once", help="Run one browse session")
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
