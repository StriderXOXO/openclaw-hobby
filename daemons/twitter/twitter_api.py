"""Twitter API wrapper (via twitterapi.io).

twitterapi.io 是一个第三方 Twitter API 代理服务（付费），
提供 REST 风格接口访问 Twitter 数据。

注册地址：https://twitterapi.io
计费：约 $0.02-0.04/天（正常使用量）

Usage:
    api = TwitterAPI(api_key="your-key")
    tweets = api.get_user_tweets(username="elonmusk")
"""

import requests
import time
import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://api.twitterapi.io"


class TwitterAPIError(Exception):
    def __init__(self, status_code, message):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Twitter API error {status_code}: {message}")


class TwitterAPI:
    def __init__(self, api_key, login_cookies=None, proxy=None):
        self.api_key = api_key
        self.login_cookies = login_cookies
        self.proxy = proxy
        self.session = requests.Session()
        self.session.headers["x-api-key"] = api_key

    def _get(self, path, params=None):
        url = f"{BASE_URL}{path}"
        for attempt in range(3):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 60))
                    logger.warning("Rate limited, waiting %ds", wait)
                    time.sleep(wait)
                    continue
                if resp.status_code != 200:
                    raise TwitterAPIError(resp.status_code, resp.text[:500])
                return resp.json()
            except requests.RequestException as e:
                if attempt == 2:
                    raise
                logger.warning("Request failed (attempt %d): %s", attempt + 1, e)
                time.sleep(5 * (attempt + 1))
        return None

    def _post(self, path, json_data=None):
        url = f"{BASE_URL}{path}"
        resp = self.session.post(url, json=json_data, timeout=30)
        if resp.status_code != 200:
            raise TwitterAPIError(resp.status_code, resp.text[:500])
        return resp.json()

    # --- User endpoints ---

    def get_user_info(self, username):
        """Look up a user profile by screen name."""
        return self._get("/twitter/user/info", {"userName": username})

    def get_user_tweets(self, user_id=None, username=None, cursor=None, include_replies=False):
        """Get recent tweets from a user (up to 20 per page)."""
        params = {}
        if user_id:
            params["userId"] = user_id
        elif username:
            params["userName"] = username
        if cursor:
            params["cursor"] = cursor
        if include_replies:
            params["includeReplies"] = "true"
        return self._get("/twitter/user/last_tweets", params)

    def get_user_followings(self, username, cursor=None):
        """Get accounts that a user follows (200 per page)."""
        params = {"userName": username}
        if cursor:
            params["cursor"] = cursor
        return self._get("/twitter/user/followings", params)

    def get_user_followers(self, username, cursor=None):
        """Get followers of a user (200 per page)."""
        params = {"userName": username}
        if cursor:
            params["cursor"] = cursor
        return self._get("/twitter/user/followers", params)

    def search_users(self, query, cursor=None):
        """Search for users by keyword."""
        params = {"query": query}
        if cursor:
            params["cursor"] = cursor
        return self._get("/twitter/user/search", params)

    # --- Tweet endpoints ---

    def search_tweets(self, query, query_type="Latest", cursor=None):
        """Search tweets by keyword (up to 20 per page)."""
        params = {"query": query, "queryType": query_type}
        if cursor:
            params["cursor"] = cursor
        return self._get("/twitter/tweet/advanced_search", params)

    def get_tweet_replies(self, tweet_id, cursor=None, query_type="Relevance"):
        """Get replies to a tweet (up to 20 per page)."""
        params = {"tweetId": tweet_id}
        if cursor:
            params["cursor"] = cursor
        if query_type != "Relevance":
            params["queryType"] = query_type
        return self._get("/twitter/tweet/replies/v2", params)

    def get_thread(self, tweet_id, cursor=None):
        """Get the full thread context for a tweet."""
        params = {"tweetId": tweet_id}
        if cursor:
            params["cursor"] = cursor
        return self._get("/twitter/tweet/thread_context", params)

    # --- Write actions ---

    def follow_user(self, user_id):
        """Follow a user. Requires login_cookies + proxy."""
        if not self.login_cookies:
            logger.warning("Cannot follow without login cookies")
            return None
        body = {
            "login_cookies": self.login_cookies,
            "user_id": user_id,
        }
        if self.proxy:
            body["proxy"] = self.proxy
        return self._post("/twitter/follow_user_v2", body)
